/**
 * UI chrome in Korean and English.
 *
 * Chrome only. The analysis prose stays in English and is not translated here,
 * because it is generated on the Python side as structured findings — a finding
 * carries an id, a basis and its evidence, and those fields are the anchor a
 * translation would later attach to. Translating the rendered sentence instead
 * would produce two texts that drift apart with nothing to check them against.
 *
 * The dictionary is a flat object typed off the English one, so a missing
 * Korean key is a compile error rather than a silent fallback to English. A
 * silent fallback is how half a UI ends up untranslated without anyone
 * noticing.
 */

export const LOCALES = ["en", "ko"] as const;
export type Locale = (typeof LOCALES)[number];

export const DEFAULT_LOCALE: Locale = "en";

const en = {
  "app.title": "osu-forge",
  "app.advisory": "Recommends only. Never modifies osu! files.",

  "nav.replays": "Replays",
  "nav.analysis": "Analysis",
  "nav.settings": "Settings",

  "replays.empty": "No replays prepared.",
  "replays.loading": "Loading…",
  "replays.count": "{n} replay(s)",
  "replays.misses": "{n} miss",

  "player.play": "Play",
  "player.pause": "Pause",
  "player.speed": "Speed",
  "player.samplesOnly": "Recorded samples only",
  "player.samplesOnlyHelp":
    "A replay records about 60 cursor positions a second and nothing between them. Points are what was measured; the faint line only joins them.",

  "windows.300": "300",
  "windows.100": "100",
  "windows.50": "50",
  "windows.miss": "Miss",
  "windows.legend": "Judgement windows, to scale",

  "settings.heading": "Hardware",
  "settings.declared": "Declared, not measured — no file on disk records any of this.",
  "settings.dpi": "Mouse DPI",
  "settings.dpiHelp": "One number is all most mice have.",
  "settings.dpiPerAxis": "Separate DPI per axis",
  "settings.dpiX": "Horizontal",
  "settings.dpiY": "Vertical",
  "settings.cutoff": "Sensor cut-off",
  "settings.cutoffNone": "Not declared",
  "settings.save": "Save",
  "settings.saved": "Saved",

  "error.unreachable": "The local server is not answering.",
  "error.unauthorised": "This page's token was rejected. Restart `forge serve`.",
} as const;

export type Key = keyof typeof en;

const ko: Record<Key, string> = {
  "app.title": "osu-forge",
  "app.advisory": "권고만 합니다. osu! 파일을 수정하지 않습니다.",

  "nav.replays": "리플레이",
  "nav.analysis": "분석",
  "nav.settings": "설정",

  "replays.empty": "준비된 리플레이가 없습니다.",
  "replays.loading": "불러오는 중…",
  "replays.count": "리플레이 {n}개",
  "replays.misses": "미스 {n}",

  "player.play": "재생",
  "player.pause": "일시정지",
  "player.speed": "속도",
  "player.samplesOnly": "기록된 표본만",
  "player.samplesOnlyHelp":
    "리플레이는 커서 위치를 초당 약 60번 기록하고 그 사이는 기록하지 않습니다. 점이 실제로 측정된 것이고, 흐린 선은 점을 잇기만 합니다.",

  "windows.300": "300",
  "windows.100": "100",
  "windows.50": "50",
  "windows.miss": "미스",
  "windows.legend": "판정 창, 실제 비율",

  "settings.heading": "하드웨어",
  "settings.declared": "측정이 아니라 선언한 값입니다 — 어느 파일에도 기록되어 있지 않습니다.",
  "settings.dpi": "마우스 DPI",
  "settings.dpiHelp": "대부분의 마우스는 이 숫자 하나면 충분합니다.",
  "settings.dpiPerAxis": "축별로 DPI 분리",
  "settings.dpiX": "가로",
  "settings.dpiY": "세로",
  "settings.cutoff": "센서 컷오프",
  "settings.cutoffNone": "미선언",
  "settings.save": "저장",
  "settings.saved": "저장됨",

  "error.unreachable": "로컬 서버가 응답하지 않습니다.",
  "error.unauthorised": "이 페이지의 토큰이 거부되었습니다. `forge serve` 를 다시 실행하세요.",
};

const DICTIONARIES: Record<Locale, Record<Key, string>> = { en, ko };

/** Look up a string, filling `{name}` placeholders. */
export function translator(locale: Locale) {
  const table = DICTIONARIES[locale] ?? DICTIONARIES[DEFAULT_LOCALE];
  return (key: Key, values?: Record<string, string | number>): string => {
    const template = table[key];
    if (!values) return template;
    return template.replace(/\{(\w+)\}/g, (whole, name: string) =>
      name in values ? String(values[name]) : whole,
    );
  };
}

/** The locale to use, from the browser, restricted to what exists. */
export function detectLocale(candidates: readonly string[]): Locale {
  for (const candidate of candidates) {
    const base = candidate.toLowerCase().split("-")[0];
    const found = LOCALES.find((locale) => locale === base);
    if (found) return found;
  }
  return DEFAULT_LOCALE;
}
