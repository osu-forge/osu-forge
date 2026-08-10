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
  "replays.filter": "Filter by artist, title or difficulty",
  "replays.noMatch": "Nothing matches that filter.",
  "replays.new": "new",

  "player.play": "Play",
  "player.pause": "Pause",
  "player.speed": "Speed",
  "player.replay": "Replay",
  "player.break": "break",
  "player.loopClear": "Clear the A–B loop",
  "player.revealHidden": "reveal HD",
  "player.liveStats": "Running totals at the playhead",
  "player.errorBar": "The last dozen hit errors on the judgement-window scale",
  "player.shortcuts":
    "Space play/pause · ←/→ seek 2 s, Shift 10 s · , and . step one sample · [ and ] mark an A–B loop, \\ clears it · Home start",
  "player.samplesOnly": "Recorded samples only",
  "player.samplesOnlyHelp":
    "A replay records about 60 cursor positions a second and nothing between them. Points are what was measured; the faint line only joins them.",

  "windows.300": "300",
  "windows.100": "100",
  "windows.50": "50",
  "windows.miss": "Miss",
  "windows.legend": "Judgement windows, to scale",

  "analysis.none": "This play could not be analysed. It can still be played back.",
  "analysis.unreproduced":
    "The simulation did not reproduce this play's judgement counts, so the numbers below describe something other than what happened.",
  "analysis.accuracy": "Accuracy",
  "analysis.unstableRate": "Unstable rate",
  "analysis.spreadHint": "Spread. An offset does not narrow it.",
  "analysis.meanError": "Mean error",
  "analysis.biasHint": "Bias. This is what an offset moves.",
  "analysis.combo": "Max combo",
  "analysis.comboReported": "game reported {n}",
  "analysis.whereLost": "Where the accuracy went",
  "analysis.whereLostHint":
    "The multiple is the share of the loss divided by the share of the objects. Above one, a group is costing more than its size predicts; at one it is doing exactly its share and says nothing.",
  "analysis.breaks": "Combo breaks",
  "analysis.histogram": "Hit error distribution",
  "analysis.histogramLabel":
    "Histogram of this play's hit errors against the judgement windows",
  "analysis.histogramCount": "{n} hit(s)",

  "keys.inputs": "The inputs",
  "keys.pressCount": "{n} press(es)",
  "keys.hold": "{ms} ms held, median",
  "keys.holdUnknown": "still down at the end",
  "keys.intervals": "Tap intervals",
  "keys.intervalsHint":
    "Real milliseconds from one tap to the next, whichever finger made it. The dashed line is this play's median; the tooltip converts a wait to the stream speed it would be at 1/4 snap. Click to jump there.",
  "keys.chartLabel":
    "Every wait between consecutive taps, across the recording, in real milliseconds",
  "keys.tooltipInterval": "{ms} ms since the last tap",
  "keys.tooltipBpm": "{bpm} BPM at 1/4 snap",
  "keys.capped": "{n} wait(s) longer than {ms} ms sit on the top edge.",
  "keys.median": "Median wait",
  "keys.burst": "Fastest eight",
  "keys.alternation": "Alternated",
  "keys.balance": "K1 share",
  "keys.pace": "{ms} ms · {bpm} BPM",
  "keys.samplingHint":
    "A replay records key state at the rendered framerate, about 60 times a second, so a press lands on the first sample at or after the finger moved — up to one sample gap late, 9 ms at the median and 16 ms at the 90th percentile. A wait carries one such gap at each end. Nothing finer than that is claimed here.",

  "corpus.title": "The corpus",
  "corpus.subtitle": "{n} replay(s) across {s} session(s)",
  "corpus.collecting": "still collecting evidence",
  "corpus.replays": "Replays",
  "corpus.sessions": "Sessions",
  "corpus.hits": "Hits",
  "corpus.effective": "Worth, independent",
  "corpus.designEffect": "Design effect {n}. Hits in one sitting move together.",
  "corpus.measurementOnly": "A measurement, not a recommendation.",
  "corpus.offsetsUnknown":
    "Local offsets could not be read from osu!.db, so every map is taken to sit at zero. A map you have nudged in game would be left out of this answer if that were known.",
  "corpus.axes": "Where the accuracy keeps going",
  "corpus.actionable": "a setting moves this",
  "corpus.notActionable": "no setting fixes this",
  "corpus.updatedAt": "updated {time}",
  "corpus.perMap": "This map, or every map",
  "corpus.shifts": "shifts on its own",
  "corpus.thinMaps": "{n} more map(s) played too little to report alone.",
  "corpus.excluded": "{n} play(s) not in this answer",
  "corpus.verification": "Did it do what it predicted",
  "corpus.predictedObserved": "predicted {p} ms · observed {o} ms",

  "progress.heading": "Did it change",
  "progress.chartLabel":
    "Mean hit error per session, with the before and after intervals around the boundary",
  "progress.before": "before",
  "progress.after": "after",
  "progress.boundarySettings": "settings changed",
  "progress.boundaryMidpoint": "midpoint",
  "progress.sideCounts": "{n} replay(s) in {s} session(s)",
  "progress.spreadNote":
    "Spread {b} → {a} ms. A description — no interval is claimed on this difference.",
  "progress.table": "The sessions, as a table",
  "progress.tableSession": "Session",
  "progress.tableSpread": "Spread",

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
  "replays.filter": "아티스트·제목·난이도로 검색",
  "replays.noMatch": "검색 결과가 없습니다.",
  "replays.new": "새 플레이",

  "player.play": "재생",
  "player.pause": "일시정지",
  "player.speed": "속도",
  "player.replay": "처음부터",
  "player.break": "브레이크",
  "player.loopClear": "A–B 구간 해제",
  "player.revealHidden": "HD 표시 해제",
  "player.liveStats": "재생 지점까지의 누적 수치",
  "player.errorBar": "판정창 눈금 위 최근 12개 히트 오차",
  "player.shortcuts":
    "Space 재생/정지 · ←/→ 2초 이동, Shift 10초 · , 와 . 표본 단위 이동 · [ 와 ] 로 A–B 구간 반복, \\ 로 해제 · Home 처음으로",
  "player.samplesOnly": "기록된 표본만",
  "player.samplesOnlyHelp":
    "리플레이는 커서 위치를 초당 약 60번 기록하고 그 사이는 기록하지 않습니다. 점이 실제로 측정된 것이고, 흐린 선은 점을 잇기만 합니다.",

  "windows.300": "300",
  "windows.100": "100",
  "windows.50": "50",
  "windows.miss": "미스",
  "windows.legend": "판정 창, 실제 비율",

  "analysis.none": "이 플레이는 분석하지 못했습니다. 재생은 가능합니다.",
  "analysis.unreproduced":
    "시뮬레이션이 이 플레이의 판정 수를 재현하지 못했습니다. 아래 숫자는 실제로 일어난 것과 다른 것을 설명합니다.",
  "analysis.accuracy": "정확도",
  "analysis.unstableRate": "불안정도(UR)",
  "analysis.spreadHint": "산포. 오프셋으로는 좁혀지지 않습니다.",
  "analysis.meanError": "평균 오차",
  "analysis.biasHint": "편향. 오프셋이 움직이는 값입니다.",
  "analysis.combo": "최대 콤보",
  "analysis.comboReported": "게임 기록 {n}",
  "analysis.whereLost": "정확도가 어디서 깎였나",
  "analysis.whereLostHint":
    "배수는 손실 비중을 오브젝트 비중으로 나눈 값입니다. 1을 넘으면 그 그룹이 크기가 예측하는 것보다 더 깎아먹고 있고, 1이면 딱 제 몫만큼이라 아무 의미도 없습니다.",
  "analysis.breaks": "콤보 브레이크",
  "analysis.histogram": "히트 오차 분포",
  "analysis.histogramLabel": "판정창 위에 그린 이 플레이의 히트 오차 히스토그램",
  "analysis.histogramCount": "히트 {n}개",

  "keys.inputs": "입력",
  "keys.pressCount": "{n}번",
  "keys.hold": "누른 시간 중앙값 {ms} ms",
  "keys.holdUnknown": "끝까지 눌린 채",
  "keys.intervals": "탭 간격",
  "keys.intervalsHint":
    "어느 손가락이든, 한 탭에서 다음 탭까지 걸린 실제 밀리초입니다. 점선은 이 플레이의 중앙값이고, 툴팁은 그 간격을 1/4 스냅 기준 스트림 속도로 바꿔 보여줍니다. 클릭하면 그 지점으로 이동합니다.",
  "keys.chartLabel": "녹화 전체에 걸친 연속 탭 사이의 간격, 실제 밀리초",
  "keys.tooltipInterval": "직전 탭에서 {ms} ms",
  "keys.tooltipBpm": "1/4 스냅 {bpm} BPM",
  "keys.capped": "{ms} ms를 넘는 간격 {n}개는 위쪽 가장자리에 붙어 있습니다.",
  "keys.median": "간격 중앙값",
  "keys.burst": "가장 빠른 8연타",
  "keys.alternation": "번갈아 친 비율",
  "keys.balance": "K1 비중",
  "keys.pace": "{ms} ms · {bpm} BPM",
  "keys.samplingHint":
    "리플레이는 키 상태를 렌더링 프레임레이트로, 초당 약 60번 기록합니다. 그래서 누름은 손가락이 움직인 뒤 첫 표본에 찍히며, 표본 간격만큼 늦습니다 — 중앙값 9 ms, 90분위 16 ms. 간격 값은 양 끝에서 각각 그만큼 흔들립니다. 그보다 더 정밀한 주장은 하지 않습니다.",

  "corpus.title": "코퍼스",
  "corpus.subtitle": "세션 {s}개에 걸친 리플레이 {n}개",
  "corpus.collecting": "아직 근거를 모으는 중",
  "corpus.replays": "리플레이",
  "corpus.sessions": "세션",
  "corpus.hits": "히트",
  "corpus.effective": "독립 표본 가치",
  "corpus.designEffect": "설계 효과 {n}. 한 세션 안의 히트는 함께 움직입니다.",
  "corpus.measurementOnly": "측정값이지 권고가 아닙니다.",
  "corpus.offsetsUnknown":
    "osu!.db에서 로컬 오프셋을 읽지 못해 모든 맵을 0으로 간주했습니다. 게임에서 오프셋을 조정한 맵이 있다면, 그 사실을 알았을 경우 이 답에서 제외되었을 것입니다.",
  "corpus.axes": "정확도가 계속 어디로 가나",
  "corpus.actionable": "설정으로 움직입니다",
  "corpus.notActionable": "설정으로는 안 고쳐집니다",
  "corpus.updatedAt": "{time} 갱신",
  "corpus.perMap": "이 맵인가, 모든 맵인가",
  "corpus.shifts": "단독으로도 치우침",
  "corpus.thinMaps": "{n}개 맵은 플레이가 적어 따로 보고하지 않습니다.",
  "corpus.excluded": "이 답에 없는 플레이 {n}개",
  "corpus.verification": "예측대로 됐나",
  "corpus.predictedObserved": "예측 {p} ms · 실제 {o} ms",

  "progress.heading": "바뀌었나",
  "progress.chartLabel": "세션별 평균 히트 오차와 경계 전후의 신뢰구간",
  "progress.before": "이전",
  "progress.after": "이후",
  "progress.boundarySettings": "설정 변경",
  "progress.boundaryMidpoint": "중간 지점",
  "progress.sideCounts": "세션 {s}개의 리플레이 {n}개",
  "progress.spreadNote":
    "산포 {b} → {a} ms. 서술일 뿐이며, 이 차이에는 구간을 주장하지 않습니다.",
  "progress.table": "세션을 표로 보기",
  "progress.tableSession": "세션",
  "progress.tableSpread": "산포",

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
