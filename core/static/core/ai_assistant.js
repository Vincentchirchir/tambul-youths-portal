(() => {
  const root = document.querySelector("[data-ai-assistant]");
  if (!root) return;

  const launcher = root.querySelector("[data-ai-assistant-open]");
  const panel = root.querySelector(".ai-assistant__panel");
  const backdrop = root.querySelector(".ai-assistant__backdrop");
  const closeButtons = root.querySelectorAll("[data-ai-assistant-close]");
  const form = root.querySelector("[data-ai-form]");
  const input = root.querySelector("[data-ai-input]");
  const sendButton = root.querySelector("[data-ai-send]");
  const messages = root.querySelector("[data-ai-messages]");
  const status = root.querySelector("[data-ai-status]");
  const suggestions = root.querySelectorAll("[data-ai-suggestion]");
  const csrfInput = form?.querySelector('input[name="csrfmiddlewaretoken"]');
  const chatUrl = root.dataset.chatUrl;
  const maxInputLength = Number(root.dataset.maxInputLength || "1000");
  const state = { isSending: false, previousFocus: null };

  const errorMessages = {
    AUTHENTICATION_REQUIRED: "Your session has expired. Please sign in again.",
    AI_CONFIGURATION_ERROR: "Tambul Assistant is not available right now.",
    AI_DISABLED: "Tambul Assistant is currently disabled.",
    AI_INVALID_MODEL_RESPONSE: "Tambul Assistant could not format a safe answer. Please try again later.",
    AI_PROVIDER_TIMEOUT: "Tambul Assistant timed out. Please try again later.",
    AI_PROVIDER_UNAVAILABLE: "Tambul Assistant is temporarily unavailable.",
    AI_PILOT_ACCESS_DENIED: "Tambul Assistant is limited to approved pilot users.",
    AI_RATE_LIMITED: "You have reached the assistant request limit. Please try again later.",
    AI_RATE_LIMIT_UNAVAILABLE: "Tambul Assistant is temporarily unavailable.",
    AI_TOOL_LIMIT_EXCEEDED: "Tambul Assistant could not finish within the allowed tool limit.",
    INVALID_JSON: "Tambul Assistant could not process that request.",
    INVALID_MESSAGE: "Enter a question before sending.",
    MESSAGE_TOO_LONG: `Keep your question under ${maxInputLength} characters.`,
    PERMISSION_DENIED: "Your account is not authorized to view that information.",
    UNSUPPORTED_FIELD: "Tambul Assistant received an unsupported request.",
  };
  const backendMessageCodes = new Set([
    "AI_DISABLED",
    "AI_RATE_LIMITED",
    "INVALID_MESSAGE",
    "MESSAGE_TOO_LONG",
    "PERMISSION_DENIED",
  ]);

  const setStatus = (message) => {
    if (!status) return;
    status.textContent = message || "";
  };

  const setBusy = (isBusy) => {
    state.isSending = isBusy;
    if (input) input.disabled = isBusy;
    if (sendButton) sendButton.disabled = isBusy;
    suggestions.forEach((button) => {
      button.disabled = isBusy;
    });
  };

  const scrollMessagesToEnd = () => {
    if (!messages) return;
    messages.scrollTop = messages.scrollHeight;
  };

  const appendMessage = (speaker, text, modifier) => {
    if (!messages) return null;

    const item = document.createElement("article");
    item.className = `ai-assistant__message ai-assistant__message--${modifier}`;

    const label = document.createElement("span");
    label.className = "ai-assistant__message-label";
    label.textContent = speaker;

    const body = document.createElement("p");
    body.className = "ai-assistant__message-text";
    body.textContent = text;

    item.appendChild(label);
    item.appendChild(body);
    messages.appendChild(item);
    scrollMessagesToEnd();
    return item;
  };

  const removeMessage = (item) => {
    if (item && item.parentNode) {
      item.parentNode.removeChild(item);
    }
  };

  const safeErrorMessage = (response, payload) => {
    const code = payload?.error?.code;
    const backendMessage = payload?.error?.message;
    if (code && backendMessageCodes.has(code) && backendMessage) {
      return backendMessage;
    }
    if (code && errorMessages[code]) return errorMessages[code];
    if (response?.status === 403) return errorMessages.AUTHENTICATION_REQUIRED;
    if (response?.status === 429) return errorMessages.AI_RATE_LIMITED;
    if (response?.status >= 500) return errorMessages.AI_PROVIDER_UNAVAILABLE;
    return backendMessage || "Tambul Assistant could not complete that request.";
  };

  const openAssistant = () => {
    if (!panel || !launcher) return;
    state.previousFocus = document.activeElement;
    panel.hidden = false;
    if (backdrop) backdrop.hidden = false;
    launcher.setAttribute("aria-expanded", "true");
    window.setTimeout(() => input?.focus(), 0);
  };

  const closeAssistant = () => {
    if (!panel || !launcher) return;
    panel.hidden = true;
    if (backdrop) backdrop.hidden = true;
    launcher.setAttribute("aria-expanded", "false");
    if (state.previousFocus && typeof state.previousFocus.focus === "function") {
      state.previousFocus.focus();
    } else {
      launcher.focus();
    }
  };

  const sendMessage = async () => {
    if (state.isSending) return;
    const message = (input?.value || "").trim();

    if (!message) {
      setStatus(errorMessages.INVALID_MESSAGE);
      input?.focus();
      return;
    }

    if (message.length > maxInputLength) {
      setStatus(errorMessages.MESSAGE_TOO_LONG);
      input?.focus();
      return;
    }

    setStatus("");
    appendMessage("You", message, "user");
    if (input) input.value = "";

    setBusy(true);
    const loading = appendMessage(
      "Tambul Assistant",
      "Checking your authorized Tambul records...",
      "assistant",
    );

    try {
      const response = await fetch(chatUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfInput?.value || "",
          "X-Requested-With": "XMLHttpRequest",
        },
        body: JSON.stringify({ message }),
      });

      let payload = null;
      try {
        payload = await response.json();
      } catch (_error) {
        payload = null;
      }

      removeMessage(loading);
      if (!response.ok || !payload?.ok) {
        appendMessage("Tambul Assistant", safeErrorMessage(response, payload), "error");
        return;
      }

      appendMessage(
        "Tambul Assistant",
        payload.message || "Tambul Assistant returned an empty response.",
        "assistant",
      );
    } catch (_error) {
      removeMessage(loading);
      appendMessage(
        "Tambul Assistant",
        "Tambul Assistant is temporarily unavailable.",
        "error",
      );
    } finally {
      setBusy(false);
      input?.focus();
    }
  };

  launcher?.addEventListener("click", openAssistant);
  closeButtons.forEach((button) => {
    button.addEventListener("click", closeAssistant);
  });

  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    sendMessage();
  });

  suggestions.forEach((button) => {
    button.addEventListener("click", () => {
      if (state.isSending) return;
      if (input) input.value = button.textContent.trim();
      input?.focus();
    });
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && panel && !panel.hidden) {
      closeAssistant();
    }
  });
})();
