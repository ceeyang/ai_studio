"""分镜阶段：按目标时长和引擎甜区算镜头预算，按剧本段落分配，逐段让 LLM 出镜头。"""
from __future__ import annotations

from sqlalchemy import delete, func, select

from backend.domain.hashing import input_hash
from backend.domain.models import Episode, Shot
from backend.domain.shots import renumber_shots
from backend.domain.slots import delete_slots
from backend.llm.schemas import StoryboardOut
from backend.pipeline.common import continue_chain, load_episode
from backend.queue.errors import JobError
from backend.queue.handlers import HANDLERS


def shot_budget(target_seconds: float, shot_seconds: float) -> int:
    return max(1, round(target_seconds / max(shot_seconds, 0.1)))


def allocate_shots(total: int, weights: list[float]) -> list[int]:
    n = len(weights)
    if n == 0:
        return []
    remaining = max(total, n) - n  # 每段先保底 1 个
    w = [max(float(x), 0.0) for x in weights]
    wsum = sum(w)
    shares = [remaining * x / wsum for x in w] if wsum > 0 else [remaining / n] * n
    floors = [int(x) for x in shares]
    rest = remaining - sum(floors)
    order = sorted(range(n), key=lambda i: (-(shares[i] - floors[i]), i))
    for i in order[:rest]:
        floors[i] += 1
    return [1 + f for f in floors]


def _last_action(db, block_id: int) -> str:
    with db.session() as s:
        sh = s.scalar(select(Shot).where(Shot.scene_block_id == block_id)
                      .order_by(Shot.order.desc(), Shot.id.desc()))
        return sh.action if sh else ""


def storyboard_prompt_fp(prompts, anchor, characters, location, text, count, shot_seconds,
                         max_seconds, prev_action) -> tuple[str, str, str]:
    """某段落这次会用到的分镜提示词及其输入指纹（不调用 LLM）。

    分镜阶段决定跳过还是重跑、重跑影响预估决定会不会动这一段，都走这一个函数，避免两边判断走偏。
    """
    system, user = prompts.render(
        "storyboard", anchor=anchor, characters=characters, location=location, text=text,
        count=count, shot_seconds=shot_seconds, max_seconds=max_seconds, prev_action=prev_action)
    return system, user, input_hash("storyboard", system, user)


@HANDLERS.register("storyboard")
def run_storyboard(ctx, payload: dict) -> dict:
    sv = ctx.services
    episode_id = int(payload["episode_id"])
    force = bool(payload.get("force"))
    manual = payload.get("manual") or {}
    only = set(payload.get("scene_block_ids") or [])
    with sv.db.session() as s:
        ep, proj = load_episode(s, episode_id)
        blocks = list(ep.scene_blocks)
        if not blocks:
            raise JobError("这一集还没有剧本段落，先跑 script")
        try:
            caps = sv.engines.video(proj.video_engine).caps
        except KeyError as e:
            raise JobError(str(e)) from None
        shot_seconds, max_seconds = caps.default_seconds, caps.max_seconds
        counts = allocate_shots(shot_budget(ep.target_seconds, shot_seconds),
                                [len(b.text) for b in blocks])
        anchor = proj.style_bible.anchor_text if proj.style_bible else ""
        characters = [{"name": c.name, "visual_prompt": c.visual_prompt or c.description}
                      for c in proj.characters]
        char_ids = {c.name: c.id for c in proj.characters}
        locs = {l.id: {"name": l.name, "time_of_day": l.time_of_day, "atmosphere": l.atmosphere,
                       "visual_prompt": l.visual_prompt} for l in proj.locations}
        plan = [{"id": b.id, "order": b.order, "text": b.text, "location": locs.get(b.location_id),
                 "count": n} for b, n in zip(blocks, counts)]
        prompts = sv.prompts(proj.slug)

    done = skipped = 0
    prev_action = ""
    for blk in plan:
        if only and blk["id"] not in only:
            prev_action = _last_action(sv.db, blk["id"]) or prev_action
            continue
        ctx.check_canceled()
        n = blk["count"]
        system, user, fp = storyboard_prompt_fp(prompts, anchor, characters, blk["location"],
                                               blk["text"], n, shot_seconds, max_seconds,
                                               prev_action)
        key = f"storyboard:{blk['id']}"
        with sv.db.session() as s:
            ep = s.get(Episode, episode_id)
            existing = s.scalar(select(func.count()).select_from(Shot)
                                .where(Shot.scene_block_id == blk["id"]))
            unchanged = (ep.stage_hashes or {}).get(key) == fp and existing > 0
        if unchanged and not force:
            ctx.log(f"段落 {blk['order'] + 1}：输入没变，跳过")
            skipped += 1
            prev_action = _last_action(sv.db, blk["id"]) or prev_action
            continue

        def check(out: StoryboardOut, n: int = n) -> None:
            if len(out.shots) != n:
                raise ValueError(f"需要正好 {n} 个镜头，实际给了 {len(out.shots)} 个")
            unknown = {c for sh in out.shots for c in sh.cast} - set(char_ids)
            if unknown:
                raise ValueError(f"cast 里有未知角色 {sorted(unknown)}，只能用：{sorted(char_ids)}")

        out = sv.llm.generate("storyboard", system, user, StoryboardOut, check=check,
                              log=ctx.log, manual=manual, key=key)
        with sv.db.session() as s:
            gone = s.scalars(select(Shot.id).where(Shot.scene_block_id == blk["id"],
                                                   Shot.origin == "llm")).all()
            delete_slots(s, "shot", gone)
            s.execute(delete(Shot).where(Shot.scene_block_id == blk["id"], Shot.origin == "llm"))
            trimmed = 0
            for j, sh in enumerate(out.shots):
                dur = min(sh.duration, max_seconds)
                dialogue = []
                for d in sh.dialogue:
                    if d.start >= dur - 0.2:
                        trimmed += 1
                        continue
                    dd = d.model_dump()
                    dd["end"] = min(dd["end"], dur)
                    dialogue.append(dd)
                s.add(Shot(
                    episode_id=episode_id, scene_block_id=blk["id"], order=blk["order"] * 1000 + j,
                    shot_size=sh.shot_size, camera_move=sh.camera_move, action=sh.action,
                    dialogue=dialogue,
                    cast=[{"character_id": char_ids[c], "variation_id": None} for c in sh.cast],
                    image_prompt=sh.image_prompt, motion_prompt=sh.motion_prompt,
                    audio_prompt=sh.audio_prompt, duration=dur,
                    transition_out=sh.transition_out))
            if trimmed:
                ctx.log(f"段落 {blk['order'] + 1}：时长截断后裁掉 {trimmed} 句超出的台词")
            ep = s.get(Episode, episode_id)
            ep.stage_hashes = {**(ep.stage_hashes or {}), key: fp}
        done += 1
        prev_action = out.shots[-1].action
        ctx.log(f"段落 {blk['order'] + 1}：{n} 个镜头")

    with sv.db.session() as s:
        ep = s.get(Episode, episode_id)
        if done:
            if not only:
                orphan = (Shot.episode_id == episode_id, Shot.scene_block_id.is_(None),
                         Shot.origin == "llm")
                delete_slots(s, "shot", s.scalars(select(Shot.id).where(*orphan)).all())
                s.execute(delete(Shot).where(*orphan))
            block_order = {b.id: b.order for b in ep.scene_blocks}
            shots = list(s.scalars(select(Shot).where(Shot.episode_id == episode_id)))
            shots.sort(key=lambda x: (block_order.get(x.scene_block_id, 10**6), x.order, x.id))
            renumber_shots(s, episode_id, [x.id for x in shots])
        ep.status = "storyboarded"
    continue_chain(ctx, episode_id, payload)
    return {"blocks_done": done, "blocks_skipped": skipped}
