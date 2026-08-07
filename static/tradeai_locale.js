(() => {
  "use strict";

  const usesSimplifiedChinese = (languages) => {
    const [preferred = ""] = Array.from(languages || []);
    return /^zh-(?:cn|sg|hans)(?:$|-)/i.test(String(preferred));
  };

  if (typeof module === "object" && module.exports) {
    module.exports = { usesSimplifiedChinese };
    return;
  }

  const stateKey = "__tradeaiLocale";
  const languages = navigator.languages?.length ? navigator.languages : [navigator.language];
  const simplified = usesSimplifiedChinese(languages);
  const mode = simplified ? "zh-Hans" : "zh-Hant";
  const sidebarLabels = simplified
    ? { expand: "展开导航栏", collapse: "收起导航栏" }
    : { expand: "展開導覽列", collapse: "收起導覽列" };
  const labelSidebarControls = () => {
    for (const [selector, label] of [
      ['[data-testid="stExpandSidebarButton"]', sidebarLabels.expand],
      ['[data-testid="stSidebarCollapseButton"] button', sidebarLabels.collapse],
    ]) {
      const control = document.querySelector(selector);
      if (!control) continue;
      if (control.getAttribute("aria-label") !== label) control.setAttribute("aria-label", label);
      if (control.getAttribute("title") !== label) control.setAttribute("title", label);
    }
  };
  const installMobileSidebarAutoClose = () => {
    if (window.__tradeaiSidebarAutoClose) return;
    window.__tradeaiSidebarAutoClose = true;
    document.addEventListener(
      "click",
      (event) => {
        if (window.innerWidth > 768 || !event.target.closest('[data-testid="stSidebar"] a[href]')) return;
        window.setTimeout(
          () => document.querySelector('[data-testid="stSidebarCollapseButton"] button')?.click(),
          50,
        );
      },
      true,
    );
  };
  installMobileSidebarAutoClose();
  const previous = window[stateKey];

  document.documentElement.lang = mode;
  if (previous?.mode === mode) {
    previous.translate?.(document.documentElement);
    labelSidebarControls();
    return;
  }
  previous?.observer?.disconnect();
  if (previous?.frame) cancelAnimationFrame(previous.frame);

  const state = { mode, observer: null, frame: 0, ready: simplified, translate: null };
  window[stateKey] = state;
  if (simplified) {
    const observer = new MutationObserver(labelSidebarControls);
    labelSidebarControls();
    observer.observe(document.documentElement, { childList: true, subtree: true });
    state.observer = observer;
    state.translate = labelSidebarControls;
    return;
  }

  const blockedSelector =
    "script,style,code,pre,textarea,[contenteditable]:not([contenteditable='false']),[data-no-localize],.ignore-opencc";
  const hanPattern = /[\u3400-\u9fff\uf900-\ufaff]/;
  const marketValuePattern =
    /^(?:人民币|人民幣|美元|港元)?[\s+\-−↑↓$¥￥€£%(),.\dA-Z/:]*(?:万|萬|亿|億)?(?:元|美元|港元|人民币|人民幣)?[\s%]*$/i;

  const start = () => {
    if (window[stateKey] !== state || !window.OpenCC) return;
    const convert = window.OpenCC.Converter({ from: "cn", to: "twp" });

    const isBlocked = (node) => {
      const element = node.nodeType === Node.ELEMENT_NODE ? node : node.parentElement;
      return !element || Boolean(element.closest(blockedSelector));
    };

    const convertValue = (value) => {
      if (!value || !hanPattern.test(value) || marketValuePattern.test(value.trim())) return value;
      return convert(value);
    };

    const translate = (root) => {
      labelSidebarControls();
      if (!root || isBlocked(root)) return;
      if (root.nodeType === Node.TEXT_NODE) {
        const next = convertValue(root.nodeValue);
        if (next !== root.nodeValue) root.nodeValue = next;
        return;
      }
      if (root.nodeType !== Node.ELEMENT_NODE) return;

      for (const attribute of ["placeholder", "title", "aria-label"]) {
        if (!root.hasAttribute(attribute)) continue;
        const current = root.getAttribute(attribute);
        const next = convertValue(current);
        if (next !== current) root.setAttribute(attribute, next);
      }

      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      let textNode = walker.nextNode();
      while (textNode) {
        if (!isBlocked(textNode)) {
          const next = convertValue(textNode.nodeValue);
          if (next !== textNode.nodeValue) textNode.nodeValue = next;
        }
        textNode = walker.nextNode();
      }

      for (const element of root.querySelectorAll("[placeholder],[title],[aria-label]")) {
        if (isBlocked(element)) continue;
        for (const attribute of ["placeholder", "title", "aria-label"]) {
          if (!element.hasAttribute(attribute)) continue;
          const current = element.getAttribute(attribute);
          const next = convertValue(current);
          if (next !== current) element.setAttribute(attribute, next);
        }
      }
    };

    const pending = new Set();
    const flush = () => {
      state.frame = 0;
      for (const node of pending) translate(node);
      pending.clear();
    };
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type === "childList") {
          for (const node of mutation.addedNodes) pending.add(node);
        } else {
          pending.add(mutation.target);
        }
      }
      if (pending.size && !state.frame) state.frame = requestAnimationFrame(flush);
    });

    translate(document.documentElement);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["placeholder", "title", "aria-label"],
      characterData: true,
      childList: true,
      subtree: true,
    });
    state.observer = observer;
    state.translate = translate;
    state.ready = true;
  };

  if (window.OpenCC) {
    start();
    return;
  }
  let loader = document.querySelector("script[data-tradeai-opencc]");
  if (!loader) {
    loader = document.createElement("script");
    loader.src = "/app/static/vendor/opencc/cn2t.js";
    loader.dataset.tradeaiOpencc = "";
    document.head.append(loader);
  }
  loader.addEventListener("load", start, { once: true });
})();
