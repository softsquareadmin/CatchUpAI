/* No API keys or third-party scripts enter the browser. */
(() => {
  const $ = id => document.getElementById(id);
  const send = (type, payload = {}) => parent.postMessage({isStreamlitMessage: true, type, ...payload}, "*");
  const value = data => send("streamlit:setComponentValue", {value: data, dataType: "json"});
  let args = {}, db, id = null, sequence = -1, savedSequence = -1;
  let capturing = false, stopped = false, starting = false, publishing = false;
  let stream, context, source, worklet, mute, flushResolve;
  let writes = Promise.resolve(), storageFailed = false, lastPublished = "", changed = 0;
  let acknowledged = -1;
  let paused = false, savedBytes = 0, cancelled = false;
  let lastRecordingId = null, analyser, waveformFrame;
  const lastRecordingKey = "__last_recording__";
  let resetPending = false, lastResetToken = null;
  try { lastResetToken = sessionStorage.getItem("CatchUpAI-recorder-reset"); } catch (_) {}
  async function resetRecorder() {
    const token = args.reset_token;
    if (!db || !token || token === lastResetToken || resetPending) return;
    resetPending = true;
    try {
      if (capturing) await cancel();
      await writes;
      await transaction(["recordings", "chunks"], "readwrite", tx => {
        tx.objectStore("recordings").clear();
        tx.objectStore("recordings").put({id: lastRecordingKey, last_id: null});
        tx.objectStore("chunks").clear();
      });
      id = lastRecordingId = null;
      savedBytes = 0; sequence = savedSequence = acknowledged = -1;
      stopped = paused = cancelled = storageFailed = false;
      lastPublished = ""; $("error").textContent = "";
      lastResetToken = token;
      try { sessionStorage.setItem("CatchUpAI-recorder-reset", token); } catch (_) {}
      value({reset: true});
      render();
    } catch (error) {
      $("error").textContent = `Reset failed: ${error.message}`;
    } finally { resetPending = false; }
  }
  const waveform = $("waveform").getContext("2d");
  const waveformSamples = new Float32Array(512);

  function transaction(storeNames, mode, action) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(storeNames, mode);
      let result;
      try { result = action(tx); } catch (error) { tx.abort(); reject(error); return; }
      tx.oncomplete = () => resolve(result);
      tx.onerror = () => reject(tx.error || new Error("Browser storage failed"));
      tx.onabort = () => reject(tx.error || new Error("Browser storage aborted"));
    });
  }
  function read(store, range) {
    return new Promise((resolve, reject) => {
      const request = db.transaction(store).objectStore(store).getAll(range);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }
  async function recordings() {
    const entries = await read("recordings");
    const finished = entries.filter(entry => entry.stopped).sort((a, b) => b.created - a.created);
    const last = entries.find(entry => entry.id === lastRecordingKey);
    lastRecordingId = last ? last.last_id : (finished.length ? finished[0].id : null);
    if (!capturing && lastRecordingId) {
      const recording = entries.find(entry => entry.id === lastRecordingId);
      savedBytes = recording.bytes || (await read("chunks", IDBKeyRange.bound([lastRecordingId, 0], [lastRecordingId, Number.MAX_SAFE_INTEGER]))).reduce((total, chunk) => total + chunk.pcm.byteLength, 0);
    }
    render();
  }
  function selection() {
    $("library").hidden = !lastRecordingId || capturing || starting;
    $("download").disabled = !lastRecordingId;
    $("again").disabled = $("record").disabled;
    $("remove").disabled = !lastRecordingId || (lastRecordingId === id && acknowledged < savedSequence);
  }
  async function deleteRecording(recordingId) {
    await transaction(["recordings", "chunks"], "readwrite", tx => {
      tx.objectStore("recordings").delete(recordingId);
      tx.objectStore("recordings").put({id: lastRecordingKey, last_id: null});
      tx.objectStore("chunks").delete(IDBKeyRange.bound([recordingId, 0], [recordingId, Number.MAX_SAFE_INTEGER]));
    });
    if (lastRecordingId === recordingId) lastRecordingId = null;
  }
  function drawWaveform() {
    const canvas = $("waveform");
    waveform.clearRect(0, 0, canvas.width, canvas.height);
    const samples = waveformSamples;
    samples.fill(0);
    if (capturing && !paused && analyser) analyser.getFloatTimeDomainData(samples);
    waveform.fillStyle = "#0872ef";
    const bars = 39, stride = Math.floor(samples.length / bars);
    for (let bar = 0; bar < bars; bar++) {
      let energy = 0;
      for (let sample = 0; sample < stride; sample++) energy += samples[bar * stride + sample] ** 2;
      const level = Math.sqrt(energy / stride);
      const height = Math.max(4, Math.min(80, level * 650));
      waveform.globalAlpha = Math.max(.12, 1 - Math.abs(bar - (bars - 1) / 2) / (bars / 2));
      waveform.beginPath();
      waveform.roundRect(bar * (canvas.width / bars) + 3, (canvas.height - height) / 2, 7, height, 4);
      waveform.fill();
    }
    if (capturing) waveformFrame = requestAnimationFrame(drawWaveform);
  }
  function render() {
    document.body.dataset.recording = String(capturing);
    document.body.dataset.paused = String(paused);
    document.body.dataset.complete = String(Boolean(lastRecordingId) && !capturing);
    const seconds = Math.floor(savedBytes / 48000);
    $("timer").textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    $("cancel").hidden = !capturing;
    $("cancel").disabled = starting;
    $("record").textContent = capturing ? "Stop recording" : "Record Live";
    $("record").disabled = !db || starting || (!capturing && (args.disabled || args.busy || (id && !stopped)));
    $("pause").textContent = paused ? "Resume" : "Pause";
    $("pause").disabled = !capturing || starting;
    let status = args.status || "Ready";
    if (status.startsWith("Reconnecting")) {
      const attempt = status.match(/attempt \d+/);
      status = attempt ? `Reconnecting (${attempt[0]})` : "Reconnecting";
    }
    if (capturing && (status === "Ready" || status === "Stopped")) status = "Connecting";
    if (paused) status = status.startsWith("Reconnecting") ? `Paused · ${status}` : "Paused";
    else if (capturing && status === "Listening") status = "Recording";
    else if (!capturing && lastRecordingId && !args.busy) status = "Recording complete";
    else if (!capturing && !lastRecordingId && !args.busy) status = "Ready";
    $("status").dataset.state = capturing && !paused && status === "Recording" ? "recording" : (paused || /Connecting|Reconnecting|Restoring|Finishing/.test(status) ? "waiting" : "ready");
    $("status").textContent = status;
    $("waveform").hidden = !capturing;
    selection();
    send("streamlit:setFrameHeight", {height: document.body.scrollHeight + 8});
  }
  async function publish() {
    if (!db || !id || publishing) return;
    if (cancelled) {
      if (args.busy || args.session_id !== id) value({session_id: id, started: true, cancelled: true, chunks: []});
      return;
    }
    publishing = true;
    try {
      await writes;
      const ack = acknowledged;
      const range = IDBKeyRange.bound([id, Math.max(0, ack + 1)], [id, Number.MAX_SAFE_INTEGER]);
      const chunks = await new Promise((resolve, reject) => {
        const request = db.transaction("chunks").objectStore("chunks").getAll(range, 10);
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      // Keep connection status fresh even when Pause stops new audio messages.
      const heartbeat = capturing || args.busy ? Math.floor(Date.now() / 2000) : 0;
      const signature = `${id}:${ack}:${savedSequence}:${stopped}:${changed}:${heartbeat}`;
      if (signature === lastPublished && !chunks.length) return;
      lastPublished = signature;
      const encode = buffer => {
        let binary = "";
        for (const byte of new Uint8Array(buffer)) binary += String.fromCharCode(byte);
        return btoa(binary);
      };
      value({session_id: id, started: true, stopped, paused, last_sequence: savedSequence, error: storageFailed ? $("error").textContent : null,
        chunks: chunks.map(chunk => ({sequence: chunk.sequence, audio: encode(chunk.pcm)}))});
    } catch (error) { await failStorage(error); }
    finally { publishing = false; }
  }
  async function release() {
    cancelAnimationFrame(waveformFrame);
    if (source) source.disconnect();
    if (stream) stream.getTracks().forEach(track => track.stop());
    if (worklet) worklet.disconnect();
    if (mute) mute.disconnect();
    if (analyser) analyser.disconnect();
    if (context && context.state !== "closed") await context.close();
    stream = context = source = worklet = mute = analyser = null;
  }
  async function failStorage(error) {
    if (storageFailed) return;
    storageFailed = true;
    capturing = false; stopped = true; starting = false; paused = false;
    await release();
    if (id && savedBytes) lastRecordingId = id;
    $("error").textContent = `Recording stopped because audio could not be saved: ${error.message}. Download the saved portion.`;
    value({session_id: id, started: Boolean(id), stopped: true, last_sequence: savedSequence,
      chunks: [], error: $("error").textContent});
    render();
  }
  async function start() {
    starting = true; storageFailed = false; $("error").textContent = ""; render();
    try {
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error("Microphone access requires HTTPS or localhost.");
      stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1}, video: false});
      context = new AudioContext({sampleRate: 24000});
      await context.resume();
      await context.audioWorklet.addModule("pcm-worklet.js");
      if (lastRecordingId) await deleteRecording(lastRecordingId);
      id = crypto.randomUUID(); sequence = savedSequence = acknowledged = -1; stopped = false; lastPublished = "";
      paused = false; savedBytes = 0; cancelled = false;
      writes = Promise.resolve();
      await transaction(["recordings"], "readwrite", tx => {
        tx.objectStore("recordings").put({id, created: Date.now(), stopped: false});
        tx.objectStore("recordings").put({id: lastRecordingKey, last_id: null});
      });
      worklet = new AudioWorkletNode(context, "pcm-recorder");
      worklet.port.onmessage = event => {
        if (event.data.flushed) { if (flushResolve) flushResolve(); return; }
        if (!event.data.pcm || storageFailed || cancelled) return;
        const chunk = {id, sequence: ++sequence, pcm: event.data.pcm};
        writes = writes.then(async () => {
          if (storageFailed) return;
          await transaction(["chunks"], "readwrite", tx => tx.objectStore("chunks").put(chunk));
          savedBytes += chunk.pcm.byteLength;
          savedSequence = chunk.sequence; render();
        }).catch(failStorage);
      };
      mute = context.createGain(); mute.gain.value = 0;
      source = context.createMediaStreamSource(stream);
      source.connect(worklet); worklet.connect(mute); mute.connect(context.destination);
      analyser = context.createAnalyser(); analyser.fftSize = 512;
      source.connect(analyser); analyser.connect(mute);
      capturing = true; starting = false;
      drawWaveform();
      stream.getAudioTracks()[0].onended = () => { if (capturing) stop(); };
      await recordings(); render(); await publish();
    } catch (error) {
      await release(); starting = false; stopped = true;
      $("error").textContent = `Could not start recording: ${error.message}`;
      render();
    }
  }
  async function flushAudio() {
    await new Promise(resolve => {
      flushResolve = resolve; worklet.port.postMessage("flush");
      setTimeout(resolve, 1000);
    });
    flushResolve = null;
    await writes;
  }
  async function togglePause() {
    if (!capturing || starting) return;
    const action = paused ? "resume" : "pause";
    starting = true; render();
    try {
      if (paused) {
        await context.resume();
        stream.getAudioTracks().forEach(track => { track.enabled = true; });
        source.connect(worklet);
        source.connect(analyser);
        paused = false;
      } else {
        source.disconnect();
        stream.getAudioTracks().forEach(track => { track.enabled = false; });
        paused = true;
        await flushAudio();
        if (storageFailed) return;
        await context.suspend();
      }
      changed++;
      await publish();
    } catch (error) {
      $("error").textContent = `Could not ${action} recording: ${error.message}`;
    } finally {
      starting = false; render();
    }
  }
  async function stop() {
    if (!capturing) return;
    capturing = false; starting = true; render();
    if (!paused) {
      source.disconnect();
      await flushAudio();
    }
    await release(); await writes;
    stopped = true; starting = false; paused = false;
    if (!storageFailed) {
      await transaction(["recordings"], "readwrite", tx => {
        const store = tx.objectStore("recordings"); const request = store.get(id);
        request.onsuccess = () => store.put({...request.result, stopped: true, bytes: savedBytes});
        store.put({id: lastRecordingKey, last_id: savedBytes ? id : null});
      });
      if (savedBytes) lastRecordingId = id;
      await publish();
    }
    render();
  }
  async function cancel() {
    if (!capturing || starting) return;
    capturing = false; starting = true; cancelled = true; render();
    await release(); await writes;
    await deleteRecording(id);
    savedBytes = 0; stopped = true; paused = false; starting = false;
    value({session_id: id, started: true, cancelled: true, chunks: []});
    render();
  }
  $("cancel").onclick = () => cancel().catch(failStorage);
  $("again").onclick = () => start().catch(failStorage);
  $("record").onclick = () => (capturing ? stop() : start()).catch(failStorage);
  $("pause").onclick = togglePause;
  $("download").onclick = async () => {
    try {
      await writes;
      const selected = lastRecordingId;
      if (!selected) return;
      const chunks = await read("chunks", IDBKeyRange.bound([selected, 0], [selected, Number.MAX_SAFE_INTEGER]));
      const size = chunks.reduce((total, chunk) => total + chunk.pcm.byteLength, 0);
      const header = new ArrayBuffer(44), view = new DataView(header);
      const text = (offset, word) => [...word].forEach((c, i) => view.setUint8(offset + i, c.charCodeAt(0)));
      text(0, "RIFF"); view.setUint32(4, 36 + size, true); text(8, "WAVE"); text(12, "fmt ");
      view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
      view.setUint32(24, 24000, true); view.setUint32(28, 48000, true); view.setUint16(32, 2, true);
      view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, size, true);
      const url = URL.createObjectURL(new Blob([header, ...chunks.map(chunk => chunk.pcm)], {type: "audio/wav"}));
      const link = document.createElement("a"); link.href = url; link.download = `CatchUpAI-${selected}.wav`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (error) { $("error").textContent = `Download failed: ${error.message}`; }
  };
  $("remove").onclick = async () => {
    try {
      const selected = lastRecordingId;
      if (!selected || $("remove").disabled) return;
      await deleteRecording(selected);
      if (id === selected) id = null;
      savedBytes = 0;
      render();
    } catch (error) { $("error").textContent = `Delete failed: ${error.message}`; }
  };
  window.addEventListener("message", event => {
    if (event.source !== parent || event.data.type !== "streamlit:render") return;
    args = event.data.args;
    resetRecorder();
    if (args.session_id === id) acknowledged = Math.max(acknowledged, args.ack);
    if (event.data.theme) {
      document.body.style.color = "#0a1637";
      document.body.style.background = "transparent";
    }
    render();
  });
  window.addEventListener("beforeunload", event => {
    if (capturing) { event.preventDefault(); event.returnValue = ""; }
  });
  const request = indexedDB.open("CatchUpAI-audio", 1);
  request.onupgradeneeded = () => {
    request.result.createObjectStore("recordings", {keyPath: "id"});
    request.result.createObjectStore("chunks", {keyPath: ["id", "sequence"]});
  };
  request.onsuccess = async () => { db = request.result; await recordings(); await resetRecorder(); render(); };
  request.onerror = () => { $("error").textContent = "Browser storage is unavailable. Recording cannot start."; };
  setInterval(publish, 500);
  send("streamlit:componentReady", {apiVersion: 1});
  send("streamlit:setFrameHeight", {height: 260});
})();
