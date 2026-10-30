'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const voiceAssistantScript = fs.readFileSync(
  path.join(__dirname, '..', 'static', 'js', 'voice-assistant.js'),
  'utf8',
);

function createVoiceAssistant() {
  const listeners = new Map();
  const statusElement = {
    dataset: {},
    disabled: false,
    setAttribute() {},
    addEventListener(name, callback) {
      listeners.set(name, callback);
    },
  };
  const utterances = [];
  const recognitions = [];
  const acknowledgements = [];

  class MockUtterance {
    constructor(text) {
      this.text = text;
    }
  }

  class MockRecognition {
    constructor() {
      recognitions.push(this);
    }

    start() {}
    abort() {}
  }

  const window = {
    SpeechRecognition: MockRecognition,
    SpeechSynthesisUtterance: MockUtterance,
    isSecureContext: true,
    speechSynthesis: {
      cancel() {},
      getVoices: () => [],
      speak(utterance) {
        utterances.push(utterance);
      },
    },
    acknowledgeAlert(alertId, source) {
      acknowledgements.push({ alertId, source });
    },
  };
  const context = {
    document: {
      getElementById: id => id === 'voiceAssistantStatus' ? statusElement : null,
    },
    navigator: {
      mediaDevices: {
        async getUserMedia() {
          return { getTracks: () => [{ stop() {} }] };
        },
      },
    },
    SpeechSynthesisUtterance: MockUtterance,
    window,
  };
  vm.runInNewContext(voiceAssistantScript, context);

  return {
    acknowledgements,
    async enable() {
      await listeners.get('click')();
    },
    onAlert: window.VoiceAssistant.onAlert,
    recognitions,
    utterances,
  };
}

for (const transcript of [
  'Acknowledge',
  'I acknowledge',
  'Acknowledged',
  'Acknowledgement',
  'Please acknowledge this alert.',
]) {
  test(`voice assistant accepts "${transcript}"`, async () => {
    const assistant = createVoiceAssistant();
    await assistant.enable();
    assistant.onAlert({ alert_id: 'alert-1', type: 'DROWSINESS' });

    const result = [{ transcript }];
    result.isFinal = true;
    assistant.recognitions[0].onresult({ resultIndex: 0, results: [result] });

    assert.deepEqual(assistant.acknowledgements, [
      { alertId: 'alert-1', source: 'voice' },
    ]);
  });
}
