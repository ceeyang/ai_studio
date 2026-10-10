import { type APIRequestContext, expect } from '@playwright/test';

// manual provider 的固定"模型输出"。结构要过 backend/llm/schemas.py 的校验：
// 场景必须在 locations 里；anchor_text ≥ 40 字符；visual_prompt ≥ 20 字符；
// 分镜正好 2 个镜头（10 秒 ÷ h3_video 默认 5 秒），image_prompt 60–180 个英文词，cast 只能用已有角色。

export const SYNOPSIS =
  '深夜的直播间，女主播林夏照着提词器念稿，突然发现屏幕上的词被人改成了她的秘密。她停顿了一秒，抬头看向导播间。';

export const STRUCTURE = {
  title: '提词器',
  characters: [{ name: '林夏', description: '二十多岁的女主播' }],
  locations: [{ name: '直播间', time_of_day: '夜', atmosphere: '安静、压抑' }],
  scenes: [{ location: '直播间', text: SYNOPSIS }],
};

export const VISUAL = {
  style: {
    anchor_text:
      'Photorealistic live-action short drama, vertical 9:16 frame, cool teal night tones with warm practical lights, soft film grain, shallow depth of field, natural skin texture.',
    palette: 'teal and amber',
    lighting: 'low-key with practical lamps',
    texture: 'fine film grain',
    character_rules: 'keep the face, hair and outfit identical in every shot',
    mood_keywords: ['tense', 'quiet'],
    negative: 'text, watermark, extra fingers',
  },
  characters: [
    {
      name: '林夏',
      visual_prompt:
        'a Chinese woman in her twenties with shoulder-length black hair, grey wool cardigan over a white blouse, small silver stud earrings',
      core_features: 'shoulder-length black hair, grey cardigan',
    },
  ],
  locations: [
    {
      name: '直播间',
      visual_prompt:
        'a small night-time live-streaming room with a ring light, a condenser microphone, two monitors and a teleprompter, no people',
    },
  ],
};

const PROMPT =
  'medium close-up, 85mm lens, eye level. A young Chinese woman in her twenties with shoulder-length black hair and a grey wool cardigan sits at a cluttered live-streaming desk, reading from a teleprompter, her eyes widening in shock. On the desk: a ring light, a condenser microphone on a boom arm, a half-empty paper coffee cup, and a smartphone on a tripod. Cold blue monitor glow from the front, warm practical lamp behind her as rim light. Muted teal and amber palette, fine film grain, shallow depth of field, photorealistic.';

export const STORYBOARD = {
  shots: [
    {
      shot_size: '近景',
      camera_move: 'slow dolly in',
      action: '林夏照着提词器念稿',
      cast: ['林夏'],
      dialogue: [{ speaker: '林夏', zh: '欢迎回到直播间', start: 0.6, end: 2.4 }],
      image_prompt: PROMPT,
      motion_prompt: 'She reads calmly, then freezes as the words change; the camera slowly pushes in and stops on her face.',
      audio_prompt: 'Quiet room tone, soft keyboard clicks, her calm voice greeting the audience.',
      duration: 5,
      transition_out: 'hard_cut',
    },
    {
      shot_size: '特写',
      camera_move: 'static',
      action: '林夏抬头看向导播间',
      cast: ['林夏'],
      dialogue: [],
      image_prompt: PROMPT,
      motion_prompt: 'She slowly raises her head and looks toward the control room window, holding her breath.',
      audio_prompt: 'Room tone drops to near silence, a faint electrical hum.',
      duration: 5,
      transition_out: 'hard_cut',
    },
  ],
};

export async function waitJob(api: APIRequestContext, id: number, until: string[]) {
  for (let i = 0; i < 100; i++) {
    const j = await (await api.get(`/api/jobs/${id}`)).json();
    if (until.includes(j.status)) return j;
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new Error(`任务 ${id} 没等到 ${until.join('/')}`);
}

/** manual LLM：提交任务后它会停在 waiting_input，按顺序把这些输出喂给它。 */
export async function answer(api: APIRequestContext, id: number, outputs: unknown[]) {
  for (const out of outputs) {
    await waitJob(api, id, ['waiting_input']);
    const r = await api.post(`/api/jobs/${id}/input`, { data: { text: JSON.stringify(out) } });
    expect(r.ok()).toBeTruthy();
  }
  return waitJob(api, id, ['done', 'failed']);
}
