// 角色抽屉的「资料」页签：从参考图写外观、名字、人物描述、核心特征、配音音色；英文外观提示词收在「高级」里。
// 「只出声，不出镜」勾选即保存：画外音、电话里的声音不用抽定妆照，分镜里不出现在画面里。
import { useState } from 'react';
import { errorText } from '../../api/client';
import { usePatchCharacter, useRefreshShotLooks } from '../../api/queries';
import type { Character, CharacterPatch } from '../../api/types';
import { EditableFields, type FieldSpec, type FieldValues } from '../../components/EditableFields';
import { VOICES } from '../../format';
import { refreshText } from './CharacterLooks';
import { PhotoLookPanel } from './PhotoLookPanel';

const CHARACTER_FIELDS: FieldSpec[] = [
  { name: 'name', label: '名字' },
  { name: 'description', label: '人物描述', kind: 'textarea', rows: 2 },
  { name: 'core_features', label: '核心特征', kind: 'textarea', rows: 2 },
  {
    name: 'voice',
    label: '配音音色',
    kind: 'select',
    options: [{ value: '', label: '（不配音）' }, ...VOICES.map((v) => ({ value: v.id, label: v.label }))],
    customLabel: '其他（手填）',
    placeholder: 'Edge TTS 音色，如 km-KH-PisethNeural',
  },
  { name: 'visual_prompt', label: '外观提示词（英文，出图时按景别填进每个出场镜头）', kind: 'textarea', rows: 5, advanced: true },
];

/** 角色资料改动转 PATCH 体：音色留空表示不配音（null）。 */
export function toCharacterPatch(changed: FieldValues): CharacterPatch {
  const out: CharacterPatch = { ...changed };
  if (changed.voice !== undefined) out.voice = changed.voice.trim() === '' ? null : changed.voice.trim();
  return out;
}

export function CharacterProfile({
  c,
  slug,
  advancedOpen = false,
  onDirtyChange,
}: {
  c: Character;
  slug: string;
  /** 一打开就展开「高级」（去补外观提示词时） */
  advancedOpen?: boolean;
  onDirtyChange: (dirty: boolean) => void;
}) {
  const patch = usePatchCharacter(slug);
  const refresh = useRefreshShotLooks();
  // 改了外貌后自动「把镜头里的外貌更新为当前造型」的结果（换了几个、哪些对不上 / 只对上一部分）
  const [refreshMsg, setRefreshMsg] = useState<string | null>(null);
  // 资料有没保存的修改时，「从参考图写外观」不直接填入
  const [dirty, setDirty] = useState(false);
  const voiceOnly = usePatchCharacter(slug);
  return (
    <div className="char-fields">
      <label className="check" title="画外音、电话里的声音：不出现在画面里，不用抽定妆照；分镜不给他外貌占位符，他的台词写成画外音">
        <input
          type="checkbox"
          checked={!!c.voice_only}
          disabled={voiceOnly.isPending}
          onChange={(e) => voiceOnly.mutate({ id: c.id, voice_only: e.target.checked })}
        />
        只出声，不出镜（画外音、电话里的声音，不用定妆照）
      </label>
      {voiceOnly.error && <p className="error small">{errorText(voiceOnly.error)}</p>}
      <PhotoLookPanel c={c} slug={slug} profileDirty={dirty} />
      <EditableFields
        fields={CHARACTER_FIELDS}
        initial={{
          name: c.name,
          description: c.description,
          visual_prompt: c.visual_prompt,
          core_features: c.core_features,
          voice: c.voice ?? '',
        }}
        advancedOpen={advancedOpen}
        saving={patch.isPending}
        error={patch.error ? errorText(patch.error) : refresh.error ? `镜头外貌没更新：${errorText(refresh.error)}` : null}
        onDirtyChange={(d) => {
          setDirty(d);
          onDirtyChange(d);
        }}
        onSave={(_v, ch) => {
          const old = c.visual_prompt;
          // 外貌改了：镜头里一字不差抄着旧外貌的地方一起换成新外貌（占位符，出图时按景别填）
          const sync = ch.visual_prompt !== undefined && ch.visual_prompt.trim() !== old.trim() && old.trim();
          patch.mutate(
            { id: c.id, ...toCharacterPatch(ch) },
            {
              onSuccess: () => {
                setRefreshMsg(null);
                if (sync) refresh.mutate({ id: c.id, old: [old] }, { onSuccess: (r) => setRefreshMsg(refreshText(r)) });
              },
            },
          );
        }}
      />
      {refreshMsg && (
        <p className="notice small" data-testid="profile-refresh-look-result">
          {refreshMsg}
        </p>
      )}
    </div>
  );
}
