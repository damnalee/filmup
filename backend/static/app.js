"use strict";

const form = document.getElementById("chat-form");
const input = document.getElementById("message-input");
const sendButton = document.getElementById("send-button");
const messages = document.getElementById("messages");
const suggestions = document.getElementById("suggestions");
const typingIndicator = document.getElementById("typing-indicator");
const voiceButton = document.getElementById("voice-button");
const voiceHint = document.getElementById("voice-hint");
const miraStage = document.getElementById("mira-stage");
const avatarStatus = document.getElementById("avatar-status");
const characterLine = document.getElementById("character-line");

let conversation = [];
let isSending = false;
let recognition = null;
let isListening = false;
let autoSendRecognizedMessage = false;
function setMood(mood, status, line) {
    miraStage.dataset.mood = mood;
    if (status) avatarStatus.textContent = status;
    if (line) characterLine.textContent = line;
}

function escapeHTML(value) {
    return String(value)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
}

function isCinemaBookingUrl(url) {
    try {
        const normalized = String(url).replace(/&amp;/gi, "&");
        const parsed = new URL(normalized, window.location.href);
        const host = parsed.hostname.toLowerCase();

        const cinemaHost = [
            "cuecinemas.com",
            "universalcinemas.com",
            "cinepax.com"
        ].some(domain => host === domain || host.endsWith("." + domain));

        return cinemaHost &&
            /\/ticketing\/visselecttickets\.aspx$/i.test(parsed.pathname);
    } catch {
        return false;
    }
}

function safeLink(url, label) {
    if (!/^https?:\/\//i.test(url)) return label;
    const visibleLabel = isCinemaBookingUrl(url) ? "Book here" : label;
    return `<a href="${url}" target="_blank" rel="noopener noreferrer">${visibleLabel}</a>`;
}

function renderInlineMarkdown(value) {
    let text = escapeHTML(value);
    const htmlFragments = [];

    const stash = (html) => {
        const key = `@@MIRA_HTML_${htmlFragments.length}@@`;
        htmlFragments.push(html);
        return key;
    };

    // Explicit Markdown links.
    text = text.replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/gi,
        (_match, label, url) => {
            const visibleLabel = isCinemaBookingUrl(url) ? "Book here" : label;
            return stash(safeLink(url, visibleLabel));
        }
    );

    // URLs wrapped in angle brackets by the model.
    text = text.replace(
        /&lt;(https?:\/\/[^\s]+?)&gt;/gi,
        (_match, url) => stash(safeLink(url, url))
    );

    // Plain booking URLs.
    text = text.replace(/https?:\/\/[^\s<]+/gi, (wholeURL) => {
        const url = wholeURL.replace(/[.,!?;:]+$/g, "");
        const punctuation = wholeURL.slice(url.length);
        const label = isCinemaBookingUrl(url) ? "Book here" : url;
        return stash(safeLink(url, label)) + punctuation;
    });

    text = text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    text = text.replace(/`([^`]+)`/g, "<code>$1</code>");
    text = text.replace(/\*(.+?)\*/g, "<em>$1</em>");

    return text.replace(/@@MIRA_HTML_(\d+)@@/g, (_match, index) => {
        return htmlFragments[Number(index)] || "";
    });
}

function tableCells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "")
        .split("|").map((cell) => cell.trim());
}

function isTableSeparator(line) {
    return line.includes("|") &&
        tableCells(line).every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s/g, "")));
}

function renderMarkdown(markdown) {
    const lines = String(markdown ?? "").replace(/\r\n/g, "\n").split("\n");
    const output = [];
    let index = 0;

    while (index < lines.length) {
        const line = lines[index];

        if (!line.trim()) {
            index++;
            continue;
        }

        if (line.trim().startsWith("|") && index + 1 < lines.length &&
            isTableSeparator(lines[index + 1])) {
            const headers = tableCells(line);
            const rows = [];
            index += 2;

            while (index < lines.length && lines[index].trim().startsWith("|")) {
                rows.push(tableCells(lines[index]));
                index++;
            }

            const headerHTML = headers
                .map((cell) => `<th scope="col">${renderInlineMarkdown(cell)}</th>`)
                .join("");

            const bodyHTML = rows.map((row) => {
                return `<tr>${headers.map((_cell, column) =>
                    `<td>${renderInlineMarkdown(row[column] ?? "")}</td>`
                ).join("")}</tr>`;
            }).join("");

            output.push(
                `<div class="message-table-wrap"><table><thead><tr>${headerHTML}</tr></thead><tbody>${bodyHTML}</tbody></table></div>`
            );
            continue;
        }

        const heading = line.match(/^(#{1,4})\s+(.+)$/);
        if (heading) {
            const level = Math.min(heading[1].length + 2, 6);
            output.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
            index++;
            continue;
        }

        if (/^\s*[-*]\s+/.test(line)) {
            const items = [];
            while (index < lines.length && /^\s*[-*]\s+/.test(lines[index])) {
                items.push(`<li>${renderInlineMarkdown(lines[index].replace(/^\s*[-*]\s+/, ""))}</li>`);
                index++;
            }
            output.push(`<ul>${items.join("")}</ul>`);
            continue;
        }

        if (/^\s*\d+\.\s+/.test(line)) {
            const items = [];
            while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
                items.push(`<li>${renderInlineMarkdown(lines[index].replace(/^\s*\d+\.\s+/, ""))}</li>`);
                index++;
            }
            output.push(`<ol>${items.join("")}</ol>`);
            continue;
        }

        const paragraph = [];
        while (
            index < lines.length &&
            lines[index].trim() &&
            !(lines[index].trim().startsWith("|") &&
              index + 1 < lines.length && isTableSeparator(lines[index + 1])) &&
            !/^#{1,4}\s+/.test(lines[index]) &&
            !/^\s*[-*]\s+/.test(lines[index]) &&
            !/^\s*\d+\.\s+/.test(lines[index])
        ) {
            paragraph.push(lines[index]);
            index++;
        }

        if (paragraph.length) {
            output.push(`<p>${paragraph.map(renderInlineMarkdown).join("<br>")}</p>`);
        }
    }

    return output.join("");
}

function scrollToLatest() {
    messages.scrollTop = messages.scrollHeight;
}

function addMessage(role, content) {
    const row = document.createElement("div");
    row.className = `message-row ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = role === "assistant" ? "M" : "YOU";

    const body = document.createElement("div");
    body.className = "message-content";

    const sender = document.createElement("span");
    sender.className = "message-sender";
    sender.textContent = role === "assistant" ? "MIRA" : "YOU";

    const bubble = document.createElement("div");
    bubble.className = "message-bubble";
    if (role === "assistant") {
        bubble.innerHTML = renderMarkdown(content);
    } else {
        // User messages are text only. Never interpret user input as HTML.
        bubble.textContent = content;
    }

    body.append(sender, bubble);
    row.append(avatar, body);
    messages.appendChild(row);
    scrollToLatest();
}

async function sendMessage(rawMessage) {
    const message = rawMessage.trim();
    if (!message || isSending) return;

    isSending = true;
    suggestions.hidden = true;

    const history = conversation.slice(-10);
    conversation.push({ role: "user", content: message });

    addMessage("user", message);
    input.value = "";
    input.disabled = true;
    sendButton.disabled = true;
    voiceButton.disabled = true;
    typingIndicator.hidden = false;
    setMood("thinking", "CONNECTING THE DOTS", "Give me a second. I'm checking the listings.");
    scrollToLatest();

    try {
        const response = await fetch("/chat", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message, history })
        });

        let data;
        try {
            data = await response.json();
        } catch {
            throw new Error("The server returned an invalid response.");
        }

        if (!response.ok) {
            throw new Error(
                typeof data.detail === "string"
                    ? data.detail
                    : "The chat service couldn't process the request."
            );
        }

        if (typeof data.answer !== "string" || !data.answer.trim()) {
            throw new Error("MIRA returned an empty answer.");
        }

        conversation.push({ role: "assistant", content: data.answer });
        conversation = conversation.slice(-10);

        addMessage("assistant", data.answer);
        setMood("talking", "MIRA HAS A THOUGHT", "Now that's a plot worth looking into.");

        window.setTimeout(() => {
            if (!isSending && !isListening) {
                setMood("idle", "HERE FOR THE PLOT", "Want to look at another screening?");
            }
        }, 2200);
    } catch (error) {
        console.error("MIRA chat error:", error);
        const messageText = "I couldn't reach the chat service just now. Make sure the CineLahore API is running, then try again.";
        addMessage("assistant", messageText);
        conversation.push({ role: "assistant", content: messageText });
        setMood("idle", "SIGNAL INTERRUPTED", "Hmm. The connection got in the way. Try me again.");
    } finally {
        isSending = false;
        typingIndicator.hidden = true;
        input.disabled = false;
        sendButton.disabled = false;
        voiceButton.disabled = false;
        input.focus();
        scrollToLatest();
    }
}

form.addEventListener("submit", (event) => {
    event.preventDefault();
    if (isSending) return;
    void sendMessage(input.value);
});

suggestions.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-prompt]");
    if (button && !isSending) void sendMessage(button.dataset.prompt || "");
});

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;

if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.lang = "en-PK";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
        isListening = true;
        autoSendRecognizedMessage = false;
        voiceButton.classList.add("is-listening");
        voiceButton.setAttribute("aria-pressed", "true");
        setMood("listening", "I'M LISTENING", "Go on. I'm all ears.");
        voiceHint.textContent = "LISTENING / SPEAK NOW";
    };

    recognition.onresult = (event) => {
        const transcript = event.results?.[0]?.[0]?.transcript?.trim();
        if (transcript) {
            input.value = transcript;
            autoSendRecognizedMessage = true;
            voiceHint.textContent = "GOT IT / SENDING";
        }
    };

    recognition.onerror = (event) => {
        autoSendRecognizedMessage = false;
        const messages = {
            "not-allowed": "MICROPHONE PERMISSION DENIED",
            "no-speech": "NO SPEECH DETECTED / TRY AGAIN",
            "network": "VOICE RECOGNITION NEEDS A NETWORK"
        };
        voiceHint.textContent = messages[event.error] || "VOICE INPUT INTERRUPTED";
        setMood("idle", "HERE FOR THE PLOT");
    };

    recognition.onend = () => {
        isListening = false;
        voiceButton.classList.remove("is-listening");
        voiceButton.setAttribute("aria-pressed", "false");

        if (autoSendRecognizedMessage && input.value.trim() && !isSending) {
            autoSendRecognizedMessage = false;
            form.requestSubmit();
        } else if (!isSending) {
            setMood("idle", "HERE FOR THE PLOT");
            voiceHint.textContent = "TEXT READY / MIC OPTIONAL";
        }
    };

    voiceButton.addEventListener("click", () => {
        if (isSending) return;

        try {
            if (isListening) recognition.stop();
            else recognition.start();
        } catch (error) {
            console.warn("Speech recognition could not start:", error);
            voiceHint.textContent = "VOICE IS BUSY / TRY AGAIN";
        }
    });
} else {
    voiceButton.disabled = true;
    voiceButton.title = "Speech recognition is not supported in this browser";
    voiceButton.setAttribute("aria-label", "Speech recognition is not supported");
    voiceHint.textContent = "TEXT READY / THIS BROWSER HAS NO MIC API";
}



const canvas = document.getElementById("liquid-canvas");
const gl = canvas.getContext("webgl", {
    alpha: true,
    antialias: false,
    powerPreference: "low-power"
});

if (gl) {
    const vertexSource = `
        attribute vec2 a_position;
        varying vec2 v_uv;
        void main() {
            v_uv = a_position * 0.5 + 0.5;
            gl_Position = vec4(a_position, 0.0, 1.0);
        }
    `;

    const fragmentSource = `
        precision mediump float;
        varying vec2 v_uv;
        uniform vec2 u_resolution;
        uniform float u_time;
        uniform vec2 u_mouse;
        uniform vec2 u_click;
        uniform float u_click_age;

        float hash(vec2 p) {
            return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
        }

        float noise(vec2 p) {
            vec2 i = floor(p);
            vec2 f = fract(p);
            f = f * f * (3.0 - 2.0 * f);
            return mix(
                mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x),
                mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x),
                f.y
            );
        }

        float fbm(vec2 p) {
            float value = 0.0;
            float amplitude = 0.5;
            for (int i = 0; i < 4; i++) {
                value += noise(p) * amplitude;
                p *= 2.02;
                amplitude *= 0.5;
            }
            return value;
        }

        void main() {
            vec2 uv = v_uv;
            float aspect = u_resolution.x / max(u_resolution.y, 1.0);
            vec2 p = uv - 0.5;
            p.x *= aspect;

            vec2 mouse = u_mouse - 0.5;
            mouse.x *= aspect;
            float mouseDistance = length(p - mouse);
            float influence = exp(-mouseDistance * 4.0);

            float time = u_time * 0.12;
            vec2 warped = p;
            warped.x += sin(p.y * 4.1 + time) * 0.055;
            warped.x += sin(p.y * 8.0 - time * 0.7) * 0.022;
            warped.y += cos(p.x * 3.5 - time) * 0.045;
            warped += (p - mouse) * influence * 0.045;

            float textureNoise = fbm(warped * 3.1 + vec2(time, -time * 0.5));

            vec2 limeCenter = vec2(-0.24 + sin(time * 1.4) * 0.12,
                                   0.18 + cos(time) * 0.1);
            vec2 coralCenter = vec2(0.27 + cos(time * 0.82) * 0.16,
                                    -0.12 + sin(time * 1.2) * 0.17);
            vec2 blueCenter = vec2(0.03 + sin(time * 0.6) * 0.26,
                                   0.30 + cos(time * 0.8) * 0.12);

            limeCenter.x *= aspect;
            coralCenter.x *= aspect;
            blueCenter.x *= aspect;

            float limeCloud = 1.0 - smoothstep(0.04, 0.63,
                length(warped - limeCenter) + textureNoise * 0.11);
            float coralCloud = 1.0 - smoothstep(0.03, 0.57,
                length(warped - coralCenter) + textureNoise * 0.09);
            float blueCloud = 1.0 - smoothstep(0.02, 0.56,
                length(warped - blueCenter) + textureNoise * 0.08);

            vec3 base = vec3(0.012, 0.019, 0.032);
            vec3 lime = vec3(0.55, 0.83, 0.25);
            vec3 coral = vec3(0.91, 0.26, 0.18);
            vec3 blue = vec3(0.18, 0.32, 0.75);

            vec3 color = base;
            color += lime * limeCloud * 0.20;
            color += coral * coralCloud * 0.16;
            color += blue * blueCloud * 0.19;

            float clickDistance = length((uv - u_click) * vec2(aspect, 1.0));
            float age = max(u_click_age, 0.0);
            float ripple = sin(clickDistance * 39.0 - age * 5.5)
                         * exp(-clickDistance * 8.0)
                         * exp(-age * 1.1);
            color += vec3(0.10, 0.13, 0.045) * ripple * step(0.0, u_click_age);

            float mouseGlow = exp(-mouseDistance * 3.5) * 0.13;
            color += vec3(0.30, 0.45, 0.28) * mouseGlow;
            color += vec3(0.015, 0.018, 0.025) * textureNoise;

            float vignette = 1.0 - smoothstep(0.32, 0.95, length(p));
            color *= vignette;
            gl_FragColor = vec4(color, 0.82);
        }
    `;

    function makeShader(type, source) {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);

        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
            console.warn("Liquid shader could not compile:", gl.getShaderInfoLog(shader));
            gl.deleteShader(shader);
            return null;
        }
        return shader;
    }

    const vertexShader = makeShader(gl.VERTEX_SHADER, vertexSource);
    const fragmentShader = makeShader(gl.FRAGMENT_SHADER, fragmentSource);

    if (vertexShader && fragmentShader) {
        const program = gl.createProgram();
        gl.attachShader(program, vertexShader);
        gl.attachShader(program, fragmentShader);
        gl.linkProgram(program);

        if (gl.getProgramParameter(program, gl.LINK_STATUS)) {
            gl.useProgram(program);
            gl.enable(gl.BLEND);
            gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);

            const buffer = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
            gl.bufferData(
                gl.ARRAY_BUFFER,
                new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
                gl.STATIC_DRAW
            );

            const position = gl.getAttribLocation(program, "a_position");
            gl.enableVertexAttribArray(position);
            gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

            const resolutionUniform = gl.getUniformLocation(program, "u_resolution");
            const timeUniform = gl.getUniformLocation(program, "u_time");
            const mouseUniform = gl.getUniformLocation(program, "u_mouse");
            const clickUniform = gl.getUniformLocation(program, "u_click");
            const clickAgeUniform = gl.getUniformLocation(program, "u_click_age");

            let targetMouse = [0.5, 0.5];
            let currentMouse = [0.5, 0.5];
            let clickPoint = [0.5, 0.5];
            let lastClickTime = -100;
            const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
            const startedAt = performance.now();

            function resizeCanvas() {
                const pixelRatio = Math.min(window.devicePixelRatio || 1, 1.5);
                const width = Math.round(window.innerWidth * pixelRatio);
                const height = Math.round(window.innerHeight * pixelRatio);

                if (canvas.width !== width || canvas.height !== height) {
                    canvas.width = width;
                    canvas.height = height;
                    gl.viewport(0, 0, width, height);
                    gl.uniform2f(resolutionUniform, width, height);
                }
            }

            window.addEventListener("resize", resizeCanvas, { passive: true });
            resizeCanvas();

            window.addEventListener("pointermove", (event) => {
                targetMouse = [
                    event.clientX / Math.max(window.innerWidth, 1),
                    1.0 - event.clientY / Math.max(window.innerHeight, 1)
                ];
            }, { passive: true });

            window.addEventListener("pointerdown", (event) => {
                clickPoint = [
                    event.clientX / Math.max(window.innerWidth, 1),
                    1.0 - event.clientY / Math.max(window.innerHeight, 1)
                ];
                lastClickTime = performance.now() / 1000;
            }, { passive: true });

            function draw(now) {
                if (document.hidden) {
                    requestAnimationFrame(draw);
                    return;
                }

                resizeCanvas();

                currentMouse[0] += (targetMouse[0] - currentMouse[0]) * 0.055;
                currentMouse[1] += (targetMouse[1] - currentMouse[1]) * 0.055;

                const time = reducedMotion ? 0.0 : (now - startedAt) / 1000;
                const clickAge = (now / 1000) - lastClickTime;

                gl.uniform1f(timeUniform, time);
                gl.uniform2f(mouseUniform, currentMouse[0], currentMouse[1]);
                gl.uniform2f(clickUniform, clickPoint[0], clickPoint[1]);
                gl.uniform1f(clickAgeUniform, clickAge);
                gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
                requestAnimationFrame(draw);
            }

            requestAnimationFrame(draw);
        } else {
            console.warn("Liquid shader program could not link:", gl.getProgramInfoLog(program));
            canvas.style.display = "none";
        }
    } else {
        canvas.style.display = "none";
    }
} else {
    canvas.style.display = "none";
    console.info("WebGL unavailable. The page background remains usable.");
}
