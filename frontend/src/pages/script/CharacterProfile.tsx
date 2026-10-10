// 角色抽屉的「资料」页签：名字、人物描述、核心特征、配音音色；英文外观提示词收在「高级」里。
import { errorText } from '../../api/client';
import { usePatchCharacter } from '../../api/queries';
import type { Character, CharacterPatch } from '../../api/types';
import { EditableFields, type FieldSpec, type FieldValues } from '../../components/EditableFields';
import { VOICES } from '../../format';

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
  { name: 'visual_prompt', label: '外观提示词（英文，会原样写进每个出场镜头）', kind: 'textarea', rows: 5, advanced: true },
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
  return (
    <div className="char-fields">
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
        error={patch.error ? errorText(patch.error) : null}
        onDirtyChange={onDirtyChange}
        onSave={(_v, ch) => patch.mutate({ id: c.id, ...toCharacterPatch(ch) })}
      />
    </div>
  );
}
