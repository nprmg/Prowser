/* Джарвис — логика окна. Python вызывает window.jarvis.receive(событие),
   окно вызывает window.pywebview.api.<метод>(). */
(() => {
  "use strict";

  // Геометрия логотипа (рамка 85×112) — та же, что в jarvis/ui/logo.py
  const BARS = [[0, 11, 74, 103], [18, 11, 45, 112], [37, 11, 48, 99], [55, 11, 0, 84], [73, 12, 55, 84]];
  const PHASES = [0.0, 1.7, 3.1, 0.9, 2.4];
  const MODE_TITLES = { auto: "Авто", gemini: "Gemini", deepseek: "DeepSeek" };
  const TOOL_LABELS = {
    open_app: "Запуск", close_app: "Закрытие", list_running_apps: "Программы", window_control: "Окно",
    get_weather: "Погода", crypto_prices: "Криптовалюта", news_headlines: "Новости", wikipedia_summary: "Википедия",
    get_exchange_rates: "Курсы валют", is_day_off: "Календарь", web_search: "Поиск", open_url: "Сайт",
    play_youtube: "YouTube", download_file: "Загрузка", search_internet: "Поиск Google",
    run_command: "Команда", type_text: "Ввод текста", press_keys: "Клавиши", scroll: "Прокрутка",
    get_clipboard: "Буфер обмена", set_clipboard: "Буфер обмена", mouse_click: "Клик", mouse_move: "Мышь",
    mouse_drag: "Перетаскивание", click_on_screen: "Нажатие", get_screen_size: "Экран", wait: "Ожидание",
    look_at_screen: "Смотрю на экран", take_screenshot: "Скриншот", get_datetime: "Время",
    get_system_info: "Состояние ПК", set_volume: "Громкость", change_volume: "Громкость", mute: "Звук",
    media_control: "Музыка", set_brightness: "Яркость", change_brightness: "Яркость", power_action: "Питание",
    list_processes: "Процессы", kill_process: "Процесс", list_directory: "Папка", open_path: "Открытие",
    find_files: "Поиск файлов", read_text_file: "Чтение файла", write_text_file: "Запись файла",
    create_folder: "Папка", delete_path: "Удаление", move_path: "Перемещение", set_timer: "Таймер",
    set_reminder: "Напоминание", list_timers: "Таймеры", cancel_timer: "Таймер", send_telegram: "Telegram",
    smart_home_devices: "Умный дом", smart_home_action: "Умный дом",
  };
  const ARG_KEYS = ["name", "city", "query", "title", "target", "url", "coins", "keys", "text", "command", "path", "at", "level", "action"];

  const $ = (sel) => document.querySelector(sel);
  const app = $("#app");
  const log = $("#log");
  const input = $("#input");
  const state = {
    status: "idle", wake: false, provider: "", since: 0, name: "Джарвис", title: "сэр",
    modes: ["auto"], mode: "auto", confirmId: null, levels: { mic: 0, speech: 0 }, shownLevel: 0,
  };

  // --- Мост к Python ---------------------------------------------------------------------------
  let api = null;
  const call = (method, ...args) => {
    if (!api || typeof api[method] !== "function") return Promise.resolve(null);
    return api[method](...args).catch((err) => { console.error(method, err); return null; });
  };

  // --- Логотип ---------------------------------------------------------------------------------
  function logoSvg() {
    const rects = BARS.map(([x, w, top, bottom], i) =>
      `<rect class="bar bar${i}" x="${x}" y="${top}" width="${w}" height="${bottom - top}"/>`).join("");
    return `<svg viewBox="-2 -14 89 140" fill="currentColor" aria-hidden="true">${rects}</svg>`;
  }
  document.querySelectorAll("[data-logo]").forEach((el) => { el.innerHTML = logoSvg(); });

  function animateLogos(now) {
    const t = now / 1000;
    const active = state.status === "listening" || state.status === "speaking";
    const target = state.status === "listening" ? state.levels.mic : state.status === "speaking" ? state.levels.speech : 0;
    state.shownLevel += (target - state.shownLevel) * 0.35; // сглаживание
    const level = state.shownLevel;
    document.querySelectorAll(".logo .bar").forEach((bar) => {
      const i = Number(bar.classList[1].slice(3));
      let scale = 1;
      if (active) {
        const wave = Math.sin(t * 9 + PHASES[i]) * 0.6 + Math.sin(t * 5.3 + PHASES[i] * 1.9) * 0.4;
        scale = 1 + (0.12 + 0.55 * level) * wave;
      }
      if (state.status !== "thinking") bar.style.transform = `scaleY(${scale.toFixed(3)})`;
      else bar.style.transform = "";
    });
    if (state.status === "listening") {
      document.querySelectorAll(".mic-bars i").forEach((bar, i) => {
        const wave = 0.5 + 0.5 * Math.sin(t * 11 + PHASES[i] * 2);
        bar.style.setProperty("--h", (0.25 + level * (0.45 + 0.55 * wave)).toFixed(3));
      });
    }
    if (state.status === "thinking") renderStatus();
    requestAnimationFrame(animateLogos);
  }
  requestAnimationFrame(animateLogos);

  // Громкость микрофона и речи — опрашиваем, только пока слушаем или говорим
  setInterval(async () => {
    if (state.status !== "listening" && state.status !== "speaking") return;
    const levels = await call("levels");
    if (levels) state.levels = levels;
  }, 70);

  // --- Статус ----------------------------------------------------------------------------------
  function renderStatus() {
    const s = state.status;
    let text = "Готов";
    if (s === "idle" && state.wake) text = `Жду «${state.name}»`;
    if (s === "listening") text = "Слушаю…";
    if (s === "speaking") text = "Говорю…";
    if (s === "thinking") {
      const secs = Math.floor((performance.now() - state.since) / 1000);
      text = ["Думаю…", state.provider, secs ? `· ${secs} с` : ""].filter(Boolean).join(" ");
    }
    const el = $("#status");
    if (el.textContent !== text) el.textContent = text;
  }

  function setStatus(status) {
    state.status = status;
    app.dataset.state = status;
    app.classList.toggle("busy", status === "thinking" || status === "speaking");
    if (status !== "listening" && status !== "speaking") state.levels = { mic: 0, speech: 0 };
    renderStatus();
  }

  // --- Сообщения -------------------------------------------------------------------------------
  function hideEmpty() { $("#empty")?.remove(); }
  function scrollDown() { log.scrollTop = log.scrollHeight; }

  function addUser(text, source) {
    hideEmpty();
    const row = document.createElement("div");
    row.className = "msg user";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (source === "voice" || source === "telegram") {
      const tag = document.createElement("span");
      tag.className = "source";
      tag.textContent = source === "voice" ? "голосом" : "из Telegram";
      bubble.append(tag);
    }
    bubble.append(document.createTextNode(text));
    row.append(bubble);
    log.append(row);
    scrollDown();
  }

  function addReply(text) {
    hideEmpty();
    const row = document.createElement("div");
    row.className = "msg jarvis";
    const wrap = document.createElement("div");
    wrap.className = "bubble";
    const who = document.createElement("div");
    who.className = "who";
    who.innerHTML = `<span class="logo">${logoSvg()}</span>`;
    who.append(document.createTextNode(state.name));
    const body = document.createElement("div");
    body.textContent = text;
    wrap.append(who, body);
    row.append(wrap);
    log.append(row);
    scrollDown();
  }

  function addTool(name, args) {
    hideEmpty();
    let group = log.lastElementChild;
    if (!group || !group.classList.contains("tools")) {
      group = document.createElement("div");
      group.className = "tools";
      log.append(group);
    }
    const chip = document.createElement("span");
    chip.className = "tool";
    const key = ARG_KEYS.find((k) => args && args[k] !== undefined && args[k] !== "");
    let detail = key ? String(args[key]) : "";
    if (detail.length > 42) detail = detail.slice(0, 40) + "…";
    chip.textContent = (TOOL_LABELS[name] || name) + (detail ? ` · ${detail}` : "");
    chip.title = `${name}(${JSON.stringify(args || {})})`;
    group.append(chip);
    scrollDown();
  }

  function addNotice(text, error) {
    hideEmpty();
    const el = document.createElement("div");
    el.className = "notice" + (error ? " error" : "");
    el.textContent = text;
    log.append(el);
    scrollDown();
  }

  let toastTimer = null;
  function toast(text) {
    const el = $("#toast");
    el.textContent = text;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  // --- Переключатель ИИ ------------------------------------------------------------------------
  function renderModes() {
    const box = $("#aiMode");
    box.innerHTML = '<span class="thumb"></span>';
    for (const mode of ["auto", "gemini", "deepseek"]) {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.dataset.mode = mode;
      b.textContent = MODE_TITLES[mode];
      b.disabled = !state.modes.includes(mode);
      b.setAttribute("aria-checked", String(mode === state.mode));
      b.title = b.disabled ? "Добавьте ключ в настройках" : "";
      b.addEventListener("click", async () => {
        if (b.disabled || state.mode === mode) return;
        state.mode = mode;
        renderModes();
        await call("set_mode", mode);
      });
      box.append(b);
    }
    requestAnimationFrame(() => {
      const active = box.querySelector('button[aria-checked="true"]');
      const thumb = box.querySelector(".thumb");
      if (active && thumb) {
        thumb.style.width = `${active.offsetWidth}px`;
        thumb.style.transform = `translateX(${active.offsetLeft}px)`;
      }
    });
  }

  // --- Настройки -------------------------------------------------------------------------------
  const form = $("#settingsForm");
  function fillSettings(s) {
    const select = $("#voiceSelect");
    select.innerHTML = "";
    for (const [key, title] of s.voice_profiles || []) {
      const option = document.createElement("option");
      option.value = key;
      option.textContent = title;
      select.append(option);
    }
    for (const el of form.elements) {
      if (!el.name || !(el.name in s)) continue;
      if (el.type === "checkbox") el.checked = Boolean(s[el.name]);
      else el.value = s[el.name] ?? "";
    }
  }
  function openSettings(open) {
    $("#settings").classList.toggle("open", open);
    $("#settings").setAttribute("aria-hidden", String(!open));
  }
  $("#btnSettings").addEventListener("click", async () => {
    const s = await call("get_settings");
    if (s) fillSettings(s);
    openSettings(true);
  });
  $("#btnSettingsClose").addEventListener("click", () => openSettings(false));
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = {};
    for (const el of form.elements) {
      if (!el.name) continue;
      data[el.name] = el.type === "checkbox" ? el.checked : el.value.trim();
    }
    const result = await call("save_settings", data);
    if (result && result.error) { toast(result.error); return; }
    openSettings(false);
    toast("Настройки сохранены");
  });

  // --- Подтверждение ---------------------------------------------------------------------------
  function showConfirm(id, text) {
    state.confirmId = id;
    $("#confirmText").textContent = text;
    $("#scrim").hidden = false;
    $("#confirm").hidden = false;
    $("#confirmYes").focus();
  }
  function answerConfirm(ok) {
    if (state.confirmId === null) return;
    const id = state.confirmId;
    state.confirmId = null;
    $("#scrim").hidden = true;
    $("#confirm").hidden = true;
    call("confirm_answer", id, ok);
  }
  $("#confirmYes").addEventListener("click", () => answerConfirm(true));
  $("#confirmNo").addEventListener("click", () => answerConfirm(false));

  // --- Ввод ------------------------------------------------------------------------------------
  function autosize() {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
    $("#btnSend").disabled = !input.value.trim();
  }
  function send(text) {
    text = (text ?? input.value).trim();
    if (!text) return;
    input.value = "";
    autosize();
    call("send", text);
  }
  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); }
  });
  $("#btnSend").addEventListener("click", () => send());
  $("#btnMic").addEventListener("click", () => call("mic"));
  $("#btnStop").addEventListener("click", () => call("stop"));
  $("#btnReset").addEventListener("click", () => {
    log.innerHTML = "";
    call("reset");
    toast("Начат новый диалог");
  });
  $("#chips").addEventListener("click", (event) => {
    if (event.target.classList.contains("chip")) send(event.target.textContent);
  });
  $("#tglWake").addEventListener("change", (event) => {
    state.wake = event.target.checked;
    renderStatus();
    call("set_wake", event.target.checked);
  });
  $("#tglTts").addEventListener("change", (event) => call("set_tts", event.target.checked));
  $("#btnMinimize").addEventListener("click", () => call("minimize"));
  $("#btnClose").addEventListener("click", () => call("close"));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      if (state.confirmId !== null) answerConfirm(false);
      else openSettings(false);
    }
  });
  autosize();

  // --- События от Python -----------------------------------------------------------------------
  function applyState(s) {
    state.name = s.assistant_name || state.name;
    state.title = s.user_title || state.title;
    state.modes = s.modes || state.modes;
    state.mode = s.mode || state.mode;
    state.wake = Boolean(s.wake);
    $("#brandName").textContent = state.name;
    document.querySelectorAll(".wake-name").forEach((el) => { el.textContent = state.name; });
    $("#hello").textContent = `Чем могу помочь, ${state.title}?`;
    $("#tglWake").checked = state.wake;
    $("#tglTts").checked = Boolean(s.tts);
    renderModes();
    renderStatus();
  }

  window.jarvis = {
    receive(evt) {
      switch (evt.type) {
        case "state": applyState(evt); break;
        case "user": addUser(evt.text, evt.source); break;
        case "reply": addReply(evt.text); break;
        case "tool": addTool(evt.name, evt.args); break;
        case "info": addNotice(evt.text, evt.error); break;
        case "status": setStatus(evt.status); break;
        case "begin": state.since = performance.now(); state.provider = ""; break;
        case "provider": state.provider = evt.name; renderStatus(); break;
        case "confirm": showConfirm(evt.id, evt.text); break;
        case "toast": toast(evt.text); break;
        default: break;
      }
    },
  };

  async function connect() {
    api = window.pywebview && window.pywebview.api;
    const s = await call("ready");
    if (s) applyState(s);
  }
  if (window.pywebview && window.pywebview.api) connect();
  else window.addEventListener("pywebviewready", connect);
  if (new URLSearchParams(location.search).has("demo") && window.jarvisDemo) window.jarvisDemo(window.jarvis);
})();
