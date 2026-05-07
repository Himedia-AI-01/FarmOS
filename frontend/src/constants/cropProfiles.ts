// 작물 × 생육단계별 표준 환경/시비 권장값.
//
// 적용 대상: AI Agent 작물 프로필 자동 입력값 + IoT 센서카드 임계 표시.
// 값 출처 (가능한 한 한국 농가 기준):
//   - 농촌진흥청(RDA) 농업기술포털 표준재배지침서  https://www.nongsaro.go.kr
//   - RDA 채소시험장 시설채소 양액재배 매뉴얼
//   - 국립식량과학원 답작/전작 작물 시비처방
//   - aT한국농수산식품유통공사 농산물 표준재배 가이드
//
// 좁은 범위가 아닌 "재배 가능 범위"의 중앙값으로 잡았다. 농가 환경(온실/노지·시기·품종)
// 에 따라 ±10~20% 변동 가능. UI 상에서는 권장값을 자동 적용한 뒤 사용자가 직접 미세조정
// 가능하도록 한다.
//
// nutrient_ratio (N:P:K) 는 비율(상대 조성)로, 절대량이 아니다. 양액 EC/pH 와 함께 적용.
// optimal_light_hours 는 일조 + 보광 합산 기준 일일 시간.
//
// 마지막 업데이트: 2026-05 (KST).

import type { CropProfile } from '@/types';

export const SUPPORTED_CROPS = [
  '감자', '고추', '들깨', '무', '배추', '벼',
  '양배추', '오이', '옥수수', '콩', '토마토', '파',
] as const;

export const GROWTH_STAGES = ['육묘기', '영양생장기', '개화기', '착과기', '수확기'] as const;

export type SupportedCrop = (typeof SUPPORTED_CROPS)[number];
export type GrowthStage = (typeof GROWTH_STAGES)[number];

type StageEnv = Omit<CropProfile, 'name' | 'growth_stage'>;

export const CROP_STAGE_PROFILE: Record<SupportedCrop, Record<GrowthStage, StageEnv>> = {
  감자: {
    육묘기:    { optimal_temp: [15, 20], optimal_humidity: [60, 75], optimal_light_hours: 11, nutrient_ratio: { N: 1.0, P: 1.5, K: 1.5 } },
    영양생장기: { optimal_temp: [17, 22], optimal_humidity: [65, 80], optimal_light_hours: 13, nutrient_ratio: { N: 1.5, P: 1.0, K: 1.5 } },
    개화기:    { optimal_temp: [17, 22], optimal_humidity: [65, 80], optimal_light_hours: 13, nutrient_ratio: { N: 1.0, P: 1.5, K: 2.0 } },
    착과기:    { optimal_temp: [15, 20], optimal_humidity: [65, 80], optimal_light_hours: 13, nutrient_ratio: { N: 0.8, P: 1.2, K: 2.5 } },
    수확기:    { optimal_temp: [15, 20], optimal_humidity: [55, 70], optimal_light_hours: 12, nutrient_ratio: { N: 0.4, P: 1.0, K: 2.0 } },
  },
  고추: {
    육묘기:    { optimal_temp: [22, 28], optimal_humidity: [65, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    영양생장기: { optimal_temp: [22, 30], optimal_humidity: [65, 80], optimal_light_hours: 13, nutrient_ratio: { N: 1.3, P: 1.0, K: 1.2 } },
    개화기:    { optimal_temp: [22, 28], optimal_humidity: [60, 80], optimal_light_hours: 14, nutrient_ratio: { N: 1.2, P: 1.2, K: 1.3 } },
    착과기:    { optimal_temp: [22, 30], optimal_humidity: [60, 80], optimal_light_hours: 14, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.5 } },
    수확기:    { optimal_temp: [20, 28], optimal_humidity: [60, 75], optimal_light_hours: 13, nutrient_ratio: { N: 0.8, P: 0.8, K: 1.5 } },
  },
  들깨: {
    육묘기:    { optimal_temp: [18, 22], optimal_humidity: [60, 70], optimal_light_hours: 11, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    영양생장기: { optimal_temp: [18, 26], optimal_humidity: [60, 75], optimal_light_hours: 11, nutrient_ratio: { N: 1.5, P: 1.0, K: 1.0 } },
    개화기:    { optimal_temp: [16, 22], optimal_humidity: [60, 75], optimal_light_hours: 10, nutrient_ratio: { N: 0.8, P: 1.2, K: 1.2 } },
    착과기:    { optimal_temp: [16, 22], optimal_humidity: [55, 70], optimal_light_hours: 10, nutrient_ratio: { N: 0.6, P: 1.0, K: 1.5 } },
    수확기:    { optimal_temp: [14, 20], optimal_humidity: [50, 65], optimal_light_hours: 10, nutrient_ratio: { N: 0.3, P: 0.8, K: 1.2 } },
  },
  무: {
    육묘기:    { optimal_temp: [15, 20], optimal_humidity: [60, 75], optimal_light_hours: 11, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    영양생장기: { optimal_temp: [17, 20], optimal_humidity: [65, 80], optimal_light_hours: 12, nutrient_ratio: { N: 1.5, P: 0.8, K: 1.2 } },
    개화기:    { optimal_temp: [15, 20], optimal_humidity: [65, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.2 } },
    착과기:    { optimal_temp: [15, 20], optimal_humidity: [65, 80], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.5 } },
    수확기:    { optimal_temp: [15, 20], optimal_humidity: [55, 70], optimal_light_hours: 12, nutrient_ratio: { N: 0.5, P: 0.8, K: 1.2 } },
  },
  배추: {
    육묘기:    { optimal_temp: [18, 22], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    영양생장기: { optimal_temp: [15, 20], optimal_humidity: [65, 80], optimal_light_hours: 13, nutrient_ratio: { N: 2.0, P: 1.0, K: 1.5 } },
    개화기:    { optimal_temp: [13, 18], optimal_humidity: [65, 80], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.5 } },
    착과기:    { optimal_temp: [15, 20], optimal_humidity: [70, 85], optimal_light_hours: 13, nutrient_ratio: { N: 1.5, P: 1.0, K: 2.0 } },
    수확기:    { optimal_temp: [13, 18], optimal_humidity: [60, 75], optimal_light_hours: 11, nutrient_ratio: { N: 0.5, P: 0.8, K: 1.5 } },
  },
  벼: {
    육묘기:    { optimal_temp: [25, 30], optimal_humidity: [80, 90], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    영양생장기: { optimal_temp: [25, 30], optimal_humidity: [70, 85], optimal_light_hours: 13, nutrient_ratio: { N: 1.5, P: 0.8, K: 1.2 } },
    개화기:    { optimal_temp: [25, 30], optimal_humidity: [75, 85], optimal_light_hours: 13, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.5 } },
    착과기:    { optimal_temp: [22, 28], optimal_humidity: [70, 80], optimal_light_hours: 13, nutrient_ratio: { N: 0.5, P: 1.0, K: 2.0 } },
    수확기:    { optimal_temp: [20, 25], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 0.2, P: 0.8, K: 1.5 } },
  },
  양배추: {
    육묘기:    { optimal_temp: [18, 22], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    영양생장기: { optimal_temp: [15, 20], optimal_humidity: [65, 80], optimal_light_hours: 13, nutrient_ratio: { N: 2.0, P: 1.0, K: 1.5 } },
    개화기:    { optimal_temp: [13, 18], optimal_humidity: [65, 80], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.5 } },
    착과기:    { optimal_temp: [13, 18], optimal_humidity: [70, 85], optimal_light_hours: 13, nutrient_ratio: { N: 1.5, P: 1.0, K: 2.0 } },
    수확기:    { optimal_temp: [13, 18], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 0.5, P: 0.8, K: 1.5 } },
  },
  오이: {
    육묘기:    { optimal_temp: [25, 28], optimal_humidity: [65, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    영양생장기: { optimal_temp: [25, 30], optimal_humidity: [70, 85], optimal_light_hours: 13, nutrient_ratio: { N: 1.3, P: 1.0, K: 1.2 } },
    개화기:    { optimal_temp: [22, 28], optimal_humidity: [65, 80], optimal_light_hours: 14, nutrient_ratio: { N: 1.2, P: 1.0, K: 1.3 } },
    착과기:    { optimal_temp: [22, 30], optimal_humidity: [65, 85], optimal_light_hours: 14, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.5 } },
    수확기:    { optimal_temp: [22, 28], optimal_humidity: [60, 80], optimal_light_hours: 14, nutrient_ratio: { N: 0.8, P: 0.8, K: 1.5 } },
  },
  옥수수: {
    육묘기:    { optimal_temp: [20, 25], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    영양생장기: { optimal_temp: [22, 28], optimal_humidity: [60, 80], optimal_light_hours: 13, nutrient_ratio: { N: 2.0, P: 1.0, K: 1.0 } },
    개화기:    { optimal_temp: [22, 30], optimal_humidity: [60, 80], optimal_light_hours: 14, nutrient_ratio: { N: 1.5, P: 1.5, K: 1.5 } },
    착과기:    { optimal_temp: [22, 28], optimal_humidity: [55, 75], optimal_light_hours: 14, nutrient_ratio: { N: 0.8, P: 1.0, K: 2.0 } },
    수확기:    { optimal_temp: [18, 25], optimal_humidity: [50, 70], optimal_light_hours: 12, nutrient_ratio: { N: 0.3, P: 0.8, K: 1.5 } },
  },
  콩: {
    육묘기:    { optimal_temp: [22, 28], optimal_humidity: [65, 80], optimal_light_hours: 12, nutrient_ratio: { N: 0.5, P: 1.5, K: 1.0 } },
    영양생장기: { optimal_temp: [22, 28], optimal_humidity: [65, 80], optimal_light_hours: 13, nutrient_ratio: { N: 0.5, P: 1.5, K: 1.0 } },
    개화기:    { optimal_temp: [22, 28], optimal_humidity: [70, 80], optimal_light_hours: 13, nutrient_ratio: { N: 0.5, P: 1.2, K: 1.5 } },
    착과기:    { optimal_temp: [22, 28], optimal_humidity: [60, 80], optimal_light_hours: 13, nutrient_ratio: { N: 0.3, P: 1.0, K: 2.0 } },
    수확기:    { optimal_temp: [18, 25], optimal_humidity: [50, 70], optimal_light_hours: 12, nutrient_ratio: { N: 0.2, P: 0.8, K: 1.5 } },
  },
  토마토: {
    육묘기:    { optimal_temp: [22, 28], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.5, K: 1.0 } },
    영양생장기: { optimal_temp: [22, 28], optimal_humidity: [60, 80], optimal_light_hours: 14, nutrient_ratio: { N: 1.3, P: 1.0, K: 1.5 } },
    개화기:    { optimal_temp: [20, 25], optimal_humidity: [60, 80], optimal_light_hours: 14, nutrient_ratio: { N: 1.0, P: 1.2, K: 1.5 } },
    착과기:    { optimal_temp: [22, 28], optimal_humidity: [60, 80], optimal_light_hours: 14, nutrient_ratio: { N: 1.0, P: 1.0, K: 2.0 } },
    수확기:    { optimal_temp: [20, 25], optimal_humidity: [60, 75], optimal_light_hours: 14, nutrient_ratio: { N: 0.8, P: 0.8, K: 2.0 } },
  },
  파: {
    육묘기:    { optimal_temp: [15, 20], optimal_humidity: [60, 75], optimal_light_hours: 11, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    영양생장기: { optimal_temp: [15, 22], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.5, P: 0.8, K: 1.0 } },
    개화기:    { optimal_temp: [15, 20], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.0, P: 1.0, K: 1.0 } },
    착과기:    { optimal_temp: [15, 22], optimal_humidity: [60, 75], optimal_light_hours: 12, nutrient_ratio: { N: 1.2, P: 1.0, K: 1.2 } },
    수확기:    { optimal_temp: [13, 20], optimal_humidity: [55, 70], optimal_light_hours: 11, nutrient_ratio: { N: 0.4, P: 0.8, K: 1.0 } },
  },
};

/** 작물 + 생육 단계 → 완성된 CropProfile 객체. */
export function getCropStageProfile(crop: SupportedCrop, stage: GrowthStage): CropProfile {
  const env = CROP_STAGE_PROFILE[crop][stage];
  return { name: crop, growth_stage: stage, ...env };
}

/** crop 이 SUPPORTED_CROPS 에 포함되는지 type-guard. */
export function isSupportedCrop(crop: string): crop is SupportedCrop {
  return (SUPPORTED_CROPS as readonly string[]).includes(crop);
}

/** stage 가 GROWTH_STAGES 에 포함되는지 type-guard. */
export function isGrowthStage(stage: string): stage is GrowthStage {
  return (GROWTH_STAGES as readonly string[]).includes(stage);
}
