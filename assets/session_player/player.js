/* Playback only. Receives a temporary signed audio URL, never an API key. */
(() => {
  const audio = document.getElementById("audio");
  const play = document.getElementById("play");
  const seek = document.getElementById("seek");
  const error = document.getElementById("error");
  let source = "", fallbackDuration = 0, seeking = false;
  const send = (type, payload = {}) => parent.postMessage({isStreamlitMessage:true,type,...payload}, "*");
  const time = value => {
    const seconds = Math.max(0, Math.floor(value || 0));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return (hours ? `${hours}:` : "") + `${String(minutes).padStart(2,"0")}:${String(seconds % 60).padStart(2,"0")}`;
  };
  function render() {
    const duration = Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : fallbackDuration;
    seek.max = String(duration || 1);
    if (!seeking) seek.value = String(audio.currentTime || 0);
    seek.style.setProperty("--progress", `${duration ? Number(seek.value) / duration * 100 : 0}%`);
    document.getElementById("position").textContent = time(Number(seek.value));
    document.getElementById("duration").textContent = time(duration);
    document.getElementById("play-icon").toggleAttribute("hidden", !audio.paused);
    document.getElementById("pause-icon").toggleAttribute("hidden", audio.paused);
    play.setAttribute("aria-label", audio.paused ? "Play session audio" : "Pause session audio");
    play.disabled = !source;
    seek.disabled = !source || !duration;
  }
  play.onclick = async () => {
    try {
      error.hidden = true;
      if (audio.paused) await audio.play(); else audio.pause();
    } catch (_) {
      error.textContent = "Audio couldn't play. Reopen this session from Recent Sessions and try again.";
      error.hidden = false;
      send("streamlit:setFrameHeight", {height:98});
    }
    render();
  };
  seek.oninput = () => { seeking = true; render(); };
  seek.onchange = () => { audio.currentTime = Number(seek.value); seeking = false; render(); };
  for (const event of ["loadedmetadata","durationchange","timeupdate","play","pause","ended"]) audio.addEventListener(event, render);
  audio.onerror = () => {
    error.textContent = "Audio is unavailable. Reopen this session from Recent Sessions to retry.";
    error.hidden = false;
    send("streamlit:setFrameHeight", {height:98});
  };
  window.addEventListener("message", event => {
    if (event.source !== parent || event.data.type !== "streamlit:render") return;
    const args = event.data.args || {};
    fallbackDuration = Number(args.duration) || 0;
    if (args.url && args.url !== source) {
      source = args.url;
      audio.src = source;
      error.hidden = true;
      audio.load();
    }
    render();
    send("streamlit:setFrameHeight", {height:error.hidden ? 76 : 98});
  });
  send("streamlit:componentReady", {apiVersion:1});
  send("streamlit:setFrameHeight", {height:76});
})();
