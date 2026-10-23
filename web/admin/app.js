(() => {
  "use strict";
  const config = window.GO_ADMIN || {};
  const element = (identity) => document.getElementById(identity);
  const escape = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
  let key = null, index = [], records = [], generation = 0, lastActivity = Date.now(), selection = 0;
  const percent = (value) => typeof value === "number" ? `${(value * 100).toFixed(2)}%` : "자료 없음";
  const points = (value) => typeof value === "number" ? `${(value * 100).toFixed(2)}%p` : "자료 없음";

  async function openFile(filename, candidate) {
    const response = await fetch(filename, { cache: "no-store", credentials: "omit" });
    if (!response.ok) throw new Error("자료 파일을 받지 못했습니다. 페이지를 새로고침해 주세요.");
    const buffer = new Uint8Array(await response.arrayBuffer());
    const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: buffer.slice(0, 12) }, candidate, buffer.slice(12));
    return JSON.parse(new TextDecoder().decode(plain));
  }

  function lock() {
    generation++; selection++; key = null; index = []; records = [];
    element("workspace").hidden = true; element("logout").hidden = true; element("login").hidden = false;
    for (const identity of ["entity", "older", "newer", "older-view", "newer-view", "differences"]) element(identity).replaceChildren();
    element("password").value = ""; element("search").value = ""; element("message").textContent = "";
    element("password").focus();
  }

  function renderRecord(record) {
    if (!record) return "";
    const data = record.data, effect = data.effect || {}, study = effect.event_study || {}, measure = effect.measure || {};
    const values = [["보관 시각", record.recorded_at], ["계산 버전", record.version],
      ["반응일", effect.reaction_day || (data.event || {}).start_ts || "-"], ["판정", effect.verdict || "-"],
      ["실제 움직임 (일별)", percent(study.actual)], ["평소 예상 (일별)", percent(study.expected)],
      ["예상과의 차이 (일별)", points(study.ar)], ["화면 판정에 사용한 고유 움직임", points(measure.value)]];
    return `<table><tbody>${values.map(([label, value]) => `<tr><th>${escape(label)}</th><td>${escape(value)}</td></tr>`).join("")}</tbody></table>
      <details><summary>전체 보관 내용 보기</summary><pre>${escape(JSON.stringify(data, null, 2))}</pre></details>`;
  }

  function flatten(value, prefix = "", output = {}) {
    if (value !== null && typeof value === "object") {
      const entries = Object.entries(value);
      if (!entries.length) output[prefix] = JSON.stringify(value);
      else for (const [name, child] of entries) flatten(child, prefix ? `${prefix}.${name}` : name, output);
    } else output[prefix] = value;
    return output;
  }

  function compare() {
    const older = records[Number(element("older").value)], newer = records[Number(element("newer").value)];
    element("older-view").innerHTML = renderRecord(older); element("newer-view").innerHTML = renderRecord(newer);
    if (!older || !newer) { element("differences").textContent = "비교할 기록이 없습니다."; return; }
    const before = flatten(older.data), after = flatten(newer.data);
    const fields = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((field) => before[field] !== after[field]);
    element("comparison-note").textContent = older.version === newer.version
      ? "같은 계산 버전입니다. 새 시세나 기사 정정 등 자료가 바뀌면서 결과가 달라질 수 있습니다."
      : "계산 버전이 다릅니다. 그 사이 입력 자료도 바뀌었을 수 있어 차이 전부를 계산법의 개선으로 볼 수는 없습니다.";
    const labels = { "effect.verdict": "판정", "effect.reaction_day": "반응일", "effect.measure.value": "판정에 사용한 고유 움직임",
      "effect.event_study.actual": "실제 움직임 (일별)", "effect.event_study.expected": "평소 예상 (일별)",
      "effect.event_study.ar": "예상과의 차이 (일별)", "effect.event_study.ci_low": "불확실성 범위 아래쪽",
      "effect.event_study.ci_high": "불확실성 범위 위쪽", "effect.event_study.pre_drift": "사전 움직임 흔적" };
    const display = (field, value) => {
      if (value === undefined || value === null) return "자료 없음";
      if (typeof value === "boolean") return value ? "있음" : "확인하지 못함";
      if (typeof value === "number" && labels[field]) return field.endsWith("actual") || field.endsWith("expected") ? percent(value) : points(value);
      return String(value);
    };
    const table = (selected, readable) => `<table><thead><tr><th>항목</th><th>이전</th><th>비교</th></tr></thead><tbody>${selected.map((field) =>
      `<tr><th>${escape(readable ? labels[field] : field)}</th><td>${escape(readable ? display(field, before[field]) : before[field] ?? "없음")}</td><td>${escape(readable ? display(field, after[field]) : after[field] ?? "없음")}</td></tr>`).join("")}</tbody></table>`;
    const mainFields = fields.filter((field) => labels[field]);
    element("differences").innerHTML = fields.length ? (mainFields.length ? table(mainFields, true) : "대표 숫자 외의 근거 자료가 바뀌었습니다.")
      + `<details><summary>바뀐 항목 전체 보기 (${fields.length}개)</summary>${table(fields, false)}</details>` : "보관된 분석 내용이 같습니다.";
  }

  async function selectEntity() {
    const identity = element("entity").value, session = generation, request = ++selection;
    records = [];
    for (const name of ["older", "newer", "older-view", "newer-view", "differences"]) element(name).replaceChildren();
    if (!identity) { element("loading").textContent = "조건에 맞는 보관 기록이 없습니다."; return; }
    if (!/^[a-f0-9]{64}$/.test(identity)) return;
    element("loading").textContent = "보관 기록을 여는 중…";
    try {
      const loaded = await openFile(`${identity}.bin`, key);
      if (session !== generation || request !== selection) return;
      records = loaded;
      for (const name of ["older", "newer"]) element(name).innerHTML = records.map((record, position) =>
        `<option value="${position}">${escape(record.recorded_at)} · ${escape(record.version)}</option>`).join("");
      element("older").value = "0"; element("newer").value = String(records.length - 1);
      element("loading").textContent = records.length > 1 ? `${records.length}개 기록을 비교할 수 있습니다.` : "아직 기록이 하나입니다. 분석이 바뀌면 비교할 수 있습니다.";
      compare();
    } catch (_) {
      if (session === generation && request === selection) element("loading").textContent = "기록을 열지 못했습니다. 새로고침 후 다시 로그인해 주세요.";
    }
  }

  function filter() {
    const query = element("search").value.trim().toLocaleLowerCase("ko-KR");
    const filtered = index.filter((item) => `${item.label} ${item.last_at}`.toLocaleLowerCase("ko-KR").includes(query));
    element("entity").innerHTML = filtered.map((item) => `<option value="${escape(item.file)}">${escape(item.label)} · ${item.count}개 기록</option>`).join("");
    selectEntity();
  }

  element("login-form").addEventListener("submit", async (event) => {
    event.preventDefault(); const session = ++generation;
    element("submit").disabled = true; element("message").textContent = "비밀번호 확인 중…";
    try {
      const password = element("password").value; element("password").value = "";
      const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveKey"]);
      const salt = Uint8Array.from(atob(config.salt), (character) => character.charCodeAt(0));
      const candidate = await crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations: config.iterations, hash: "SHA-256" }, base, { name: "AES-GCM", length: 256 }, false, ["decrypt"]);
      const loaded = await openFile("index.bin", candidate);
      if (session !== generation) return;
      key = candidate; index = loaded; lastActivity = Date.now();
      element("login").hidden = true; element("workspace").hidden = false; element("logout").hidden = false;
      filter();
    } catch (_) {
      if (session === generation) element("message").textContent = "비밀번호가 맞지 않거나 자료를 열 수 없습니다.";
    } finally { element("submit").disabled = false; }
  });
  element("logout").addEventListener("click", lock);
  element("search").addEventListener("input", filter); element("entity").addEventListener("change", selectEntity);
  for (const name of ["older", "newer"]) element(name).addEventListener("change", compare);
  for (const name of ["pointerdown", "keydown"]) document.addEventListener(name, () => { lastActivity = Date.now(); });
  setInterval(() => { if (key && Date.now() - lastActivity > 15 * 60 * 1000) lock(); }, 10000);
  if (!config.enabled) {
    element("submit").disabled = true; element("password").disabled = true;
    element("message").textContent = "관리자 비밀번호 설정 후 자동 실행이 완료되어야 사용할 수 있습니다.";
  }
})();
