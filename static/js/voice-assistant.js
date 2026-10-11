(() => {
  'use strict';

  const statusElement = document.getElementById('voiceAssistantStatus');
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const canSpeak = Boolean(window.speechSynthesis && window.SpeechSynthesisUtterance);
  const canListen = Boolean(SpeechRecognition);
  const acknowledgementPhrases = ['drms', 'acknowledge', 'i accept', 'drms i accept'];

  let activeAlert = null;
  let generation = 0;
  let recognition = null;
  let unavailable = false;
  let enabled = false;

  function setStatus(state, title) {
    if (!statusElement) return;
    statusElement.dataset.state = state;
    statusElement.textContent = !enabled
      ? 'VOICE ASSISTANT: OFF'
      : state === 'acknowledged'
        ? 'VOICE ASSISTANT: ACKNOWLEDGED'
        : state === 'listening'
          ? 'VOICE ASSISTANT: LISTENING'
          : state === 'unavailable'
            ? canSpeak ? 'VOICE: ANNOUNCEMENTS ON' : 'VOICE ASSISTANT: UNAVAILABLE'
            : 'VOICE ASSISTANT: ON';
    statusElement.setAttribute('aria-pressed', String(enabled));
    if (title) statusElement.title = title;
  }

  async function toggleEnabled() {
    if (enabled) {
      enabled = false;
      unavailable = false;
      finishActiveAlert(null);
      setStatus(canSpeak ? 'ready' : 'unavailable',
        'Voice assistant is off. Click to turn it on.');
      return;
    }
    if (!canSpeak && !canListen) {
      unavailable = true;
      setStatus('unavailable',
        'This browser does not support speech synthesis or speech recognition. Dashboard beeps and acknowledgement buttons still work.');
      return;
    }

    enabled = true;
    unavailable = false;
    if (!canListen) {
      unavailable = true;
      setStatus('unavailable',
        canSpeak
          ? 'Spoken safety warnings are enabled. This browser does not support voice input; use Chrome or Edge for voice acknowledgement. Dashboard buttons remain available.'
          : 'Speech recognition is unavailable in this browser. Dashboard beeps and acknowledgement buttons remain available.');
      return;
    }
    if (!navigator.mediaDevices ||
        typeof navigator.mediaDevices.getUserMedia !== 'function' ||
        !window.isSecureContext) {
      unavailable = true;
      setStatus('unavailable',
        canSpeak
          ? 'Spoken safety warnings are enabled, but microphone access requires HTTPS or localhost and a supported browser. Dashboard buttons remain available.'
          : 'Microphone input requires HTTPS or localhost and a supported browser.');
      return;
    }

    statusElement.disabled = true;
    setStatus('ready', 'Requesting microphone permission. Audio is used only to acknowledge active alerts.');
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      unavailable = false;
      setStatus('ready',
        'Voice assistant is on. It announces alerts and listens only while an alert is active. Click to turn it off.');
      if (activeAlert && activeAlert.announcementDone) {
        startRecognition(activeAlert.id, generation);
      }
    } catch (error) {
      unavailable = true;
      const message = error && (error.name === 'NotAllowedError' ||
        error.name === 'PermissionDeniedError')
        ? 'Spoken safety warnings are enabled, but microphone permission was denied. Allow mic access in browser site settings, then turn voice input on again.'
        : 'Spoken safety warnings are enabled, but microphone access failed. Check browser site permissions and try again.';
      setStatus('unavailable', message);
    } finally {
      if (stream) stream.getTracks().forEach(track => track.stop());
      statusElement.disabled = false;
    }
  }

  function normalizeTranscript(transcript) {
    return transcript.toLowerCase()
      .replace(/[^a-z0-9\s]/g, ' ')
      .trim()
      .replace(/\s+/g, ' ');
  }

  function isAcknowledgement(transcript) {
    const words = normalizeTranscript(transcript).split(' ').filter(Boolean);
    return acknowledgementPhrases.some(phrase => {
      const phraseWords = phrase.split(' ');
      for (let start = 0; start <= words.length - phraseWords.length; start += 1) {
        if (phraseWords.every((word, offset) => words[start + offset] === word)) {
          return true;
        }
      }
      return false;
    });
  }

  function warningFor(alert) {
    const messages = {
      SLEEPING: 'Warning! Signs of drowsiness detected. Please focus on driving.',
      DROWSINESS: 'Warning! Drowsiness detected. Please focus on driving.',
      YAWNING: 'Frequent yawning detected. Please stay alert and consider stopping safely if you feel sleepy.',
      PHONE_USAGE: 'Phone usage detected. Please keep your attention on the road.',
      DISTRACTION: 'Please focus on the road. Driver distraction detected.',
      LANE_DEVIATION: 'Lane deviation detected. Please check your lane position.',
      OVER_SPEED: 'Vehicle speed is too high. Please slow down safely.',
      PEDESTRIAN_DETECTED: 'Pedestrian detected nearby. Please drive carefully.',
    };
    return messages[alert.type] || 'Safety warning detected. Please focus on driving.';
  }

  function speak(message, onEnd) {
    if (!canSpeak) {
      if (onEnd) onEnd();
      return;
    }
    try {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(message);
      utterance.lang = 'en-IN';
      const voices = window.speechSynthesis.getVoices();
      utterance.voice = voices.find(voice => voice.lang.toLowerCase() === 'en-in') ||
        voices.find(voice => voice.lang.toLowerCase().startsWith('en-')) || null;
      utterance.onend = onEnd || null;
      utterance.onerror = () => {
        setStatus('unavailable',
          'Speech playback failed. Check browser audio settings; dashboard beeps and buttons remain available.');
        if (onEnd) onEnd();
      };
      window.speechSynthesis.speak(utterance);
    } catch (error) {
      setStatus('unavailable',
        'Speech playback is unavailable. Dashboard beeps and buttons remain available.');
      if (onEnd) onEnd();
    }
  }

  function stopRecognition() {
    const currentRecognition = recognition;
    recognition = null;
    if (!currentRecognition) return;
    currentRecognition.onresult = null;
    currentRecognition.onerror = null;
    currentRecognition.onend = null;
    try {
      currentRecognition.abort();
    } catch (error) {
      console.debug('Speech recognition already stopped.', error);
    }
  }

  function finishActiveAlert(state) {
    generation += 1;
    activeAlert = null;
    stopRecognition();
    if (canSpeak) window.speechSynthesis.cancel();
    if (state) setStatus(state);
  }

  function startRecognition(alertId, currentGeneration) {
    if (!activeAlert || activeAlert.id !== alertId || generation !== currentGeneration) return;
    if (!enabled || !canListen) {
      unavailable = true;
      setStatus('unavailable',
        canSpeak
          ? 'Spoken safety warnings are enabled, but this browser does not support voice input. Use Chrome or Edge for voice acknowledgement.'
          : 'Voice input is unavailable. Use Chrome or Edge, grant microphone permission, and open the app on HTTPS or localhost.');
      return;
    }

    const nextRecognition = new SpeechRecognition();
    recognition = nextRecognition;
    nextRecognition.lang = 'en-IN';
    nextRecognition.continuous = true;
    nextRecognition.interimResults = false;
    nextRecognition.maxAlternatives = 3;

    nextRecognition.onresult = event => {
      if (!activeAlert || activeAlert.id !== alertId || generation !== currentGeneration) return;
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        if (!result.isFinal) continue;
        const matched = Array.from(result).some(alternative =>
          isAcknowledgement(alternative.transcript)
        );
        if (matched) {
          window.acknowledgeAlert(alertId, 'voice');
          return;
        }
      }
    };

    nextRecognition.onerror = event => {
      if (!activeAlert || activeAlert.id !== alertId || generation !== currentGeneration) return;
      if (event.error === 'no-speech' || event.error === 'aborted') return;
      unavailable = true;
      const messages = {
        'not-allowed': 'Spoken warnings remain on, but microphone access was denied. Allow microphone access in site settings, then toggle voice assistant off and on.',
        'service-not-allowed': 'Spoken warnings remain on, but the browser speech recognition service is blocked. Try Chrome or Edge and check internet access.',
        network: 'Spoken warnings remain on, but speech recognition cannot connect to its browser service. Check internet access and try again.',
        'audio-capture': 'Spoken warnings remain on, but no usable microphone was found. Connect or enable a microphone.',
        'language-not-supported': 'Spoken warnings remain on, but this browser does not support English speech recognition. Try Chrome or Edge.',
      };
      const message = messages[event.error] ||
        `Speech recognition stopped (${event.error || 'unknown error'}). Check microphone permissions and browser support.`;
      finishActiveAlert('unavailable');
      setStatus('unavailable', message);
    };

    nextRecognition.onend = () => {
      if (recognition !== nextRecognition || !activeAlert ||
          activeAlert.id !== alertId || generation !== currentGeneration) return;
      recognition = null;
      if (!unavailable) startRecognition(alertId, currentGeneration);
    };

    try {
      nextRecognition.start();
      setStatus('listening', 'Say “DRMS”, “Acknowledge”, “I accept”, or “DRMS, I accept” to acknowledge this alert.');
    } catch (error) {
      unavailable = true;
      finishActiveAlert('unavailable');
      setStatus('unavailable',
        'Spoken warnings remain on, but the browser could not start speech recognition. Check microphone permission and try Chrome or Edge over HTTPS or localhost.');
    }
  }

  function onAlert(alert) {
    if (!enabled) return;
    const alertId = alert && alert.alert_id != null ? String(alert.alert_id) : '';
    if (!alertId) return;

    finishActiveAlert(null);
    unavailable = false;
    activeAlert = { id: alertId, announcementDone: false };
    const currentGeneration = generation;
    const message = warningFor(alert);

    if (canSpeak) {
      speak(message, () => {
        if (!activeAlert || activeAlert.id !== alertId ||
            generation !== currentGeneration) return;
        activeAlert.announcementDone = true;
        startRecognition(alertId, currentGeneration);
      });
    } else {
      setStatus('unavailable', 'Speech synthesis is unavailable in this browser.');
      activeAlert.announcementDone = true;
      if (enabled) startRecognition(alertId, currentGeneration);
    }
  }

  function onAcknowledged(alertId, source) {
    if (!activeAlert || activeAlert.id !== String(alertId)) return;
    finishActiveAlert('acknowledged');
    if (source === 'voice') speak('Voice acknowledgement received.');
  }

  function onCancelled(alertId) {
    if (!activeAlert || activeAlert.id !== String(alertId)) return;
    finishActiveAlert(enabled ? 'ready' : canSpeak ? 'ready' : 'unavailable');
  }

  function onEscalation(alert) {
    if (!enabled || !activeAlert || !alert ||
        activeAlert.id !== String(alert.alert_id)) return;
    finishActiveAlert(null);
    speak('The safety alert has not been acknowledged. Please respond now.', () => {
      if (!unavailable) setStatus('ready');
    });
  }

  if (statusElement) statusElement.addEventListener('click', toggleEnabled);
  setStatus(canSpeak ? 'ready' : 'unavailable',
    canSpeak
      ? 'Voice assistant is off. Click to enable spoken alerts and voice acknowledgement.'
      : 'Speech synthesis is unavailable. Voice acknowledgement may still work after enabling with a supported browser.');
  window.VoiceAssistant = { onAlert, onAcknowledged, onCancelled, onEscalation };
})();
