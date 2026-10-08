(() => {
  const menuButton = document.querySelector('[data-menu-toggle]');
  const overlay = document.querySelector('[data-menu-close]');

  const setMenuOpen = open => {
    document.body.classList.toggle('menu-open', open);
    if (menuButton) menuButton.setAttribute('aria-expanded', String(open));
  };

  if (menuButton) {
    menuButton.addEventListener('click', () => {
      setMenuOpen(menuButton.getAttribute('aria-expanded') !== 'true');
    });
  }

  if (overlay) overlay.addEventListener('click', () => setMenuOpen(false));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') setMenuOpen(false);
  });

  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  const interview = document.querySelector('[data-mock-interview]');
  if (!interview) return;

  const setup = interview.querySelector('[data-interview-setup]');
  const startForm = interview.querySelector('[data-interview-start]');
  const session = interview.querySelector('[data-interview-session]');
  const cameraPreview = interview.querySelector('[data-camera-preview]');
  const screenPreview = interview.querySelector('[data-screen-preview]');
  const questionText = interview.querySelector('[data-interview-question]');
  const questionNumberLabel = interview.querySelector('[data-question-number]');
  const answerForm = interview.querySelector('[data-answer-form]');
  const answerButton = interview.querySelector('[data-answer-submit]');
  const recordStartButton = interview.querySelector('[data-record-start]');
  const recordStopButton = interview.querySelector('[data-record-stop]');
  const recordingPreview = interview.querySelector('[data-recording-preview]');
  const recordingStatus = interview.querySelector('[data-recording-status]');
  const sessionRating = interview.querySelector('[data-session-rating]');
  const sessionStatus = interview.querySelector('[data-session-status]');
  const setupStatus = interview.querySelector('[data-interview-status]');
  const feedbackList = interview.querySelector('[data-feedback-list]');
  const resultsDialog = interview.querySelector('[data-results-dialog]');
  const resultsStatus = interview.querySelector('[data-results-status]');
  const resultsTitle = interview.querySelector('[data-results-title]');
  const resultsRating = interview.querySelector('[data-results-rating]');
  const resultsList = interview.querySelector('[data-results-list]');
  const csrfToken = interview.querySelector('[name="csrfmiddlewaretoken"]').value;
  const topicSelect = interview.querySelector('[data-topic-select]');
  const otherTopic = interview.querySelector('[data-other-topic]');
  const codePanel = interview.querySelector('[data-code-panel]');
  const voicePanel = interview.querySelector('[data-voice-panel]');
  const codeLanguage = interview.querySelector('[data-code-language]');
  const codeEditor = interview.querySelector('[data-code-editor]');
  const codeRunButton = interview.querySelector('[data-code-run]');
  const codeSubmitButton = interview.querySelector('[data-code-submit]');
  const codeOutput = interview.querySelector('[data-code-output]');
  const codeNote = interview.querySelector('[data-code-note]');
  const maxQuestions = 10;
  let cameraStream;
  let screenStream;
  let recorder;
  let recordingChunks = [];
  let recordingBlob;
  let recordingUrl;
  let recordingTimer;
  let recordingSeconds = 0;
  let role = '';
  let difficulty = 'medium';
  let sessionId = null;
  window.__mockEnd = message => endSession(message);
  window.__mockIntegrity = () => ({ sessionId, question: typeof questionNumber === 'number' ? questionNumber : null });
  let questions = [];
  let pendingFeedback = [];
  let failedQuestionNumbers = [];
  let submittedAnswers = 0;
  let ending = false;
  let questionNumber = 1;
  let currentQuestion = '';
  let currentQuestionData = null;
  let lastRunOutput = '';
  let pythonWorker = null;
  let sqlEngine = null;
  let history = [];
  let busy = false;

  const setStatus = (element, message, isError = false) => {
    element.textContent = message;
    element.classList.toggle('is-error', isError);
  };

  const stopCapture = () => {
    if (recorder?.state === 'recording') recorder.stop();
    if (recordingTimer) window.clearInterval(recordingTimer);
    [cameraStream, screenStream].forEach(stream => {
      if (stream) stream.getTracks().forEach(track => track.stop());
    });
    cameraStream = null;
    screenStream = null;
    cameraPreview.srcObject = null;
    screenPreview.srcObject = null;
  };

  const clearRecording = () => {
    if (recordingUrl) URL.revokeObjectURL(recordingUrl);
    recordingUrl = null;
    recordingBlob = null;
    recordingChunks = [];
    recordingPreview.removeAttribute('src');
    recordingPreview.hidden = true;
    recordStartButton.disabled = false;
    recordStopButton.disabled = true;
    answerButton.disabled = true;
    setStatus(recordingStatus, '');
  };

  const captureFrame = video => {
    if (!video.videoWidth || !video.videoHeight) throw new Error('Wait for both previews to become ready.');
    const scale = Math.min(1, 640 / video.videoWidth);
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(video.videoWidth * scale);
    canvas.height = Math.round(video.videoHeight * scale);
    canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.55);
  };

  const blobToDataUrl = blob => new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener('load', () => resolve(reader.result));
    reader.addEventListener('error', () => reject(new Error('Could not read the recording.')));
    reader.readAsDataURL(blob);
  });

  const updateSessionRating = rating => {
    if (typeof rating !== 'number') return;
    sessionRating.textContent = `Saved rating ${rating.toFixed(1)}/5`;
    sessionRating.hidden = false;
  };

  const requestAI = async payload => {
    const response = await fetch(interview.dataset.aiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
      body: JSON.stringify({ ...payload, role, difficulty, consent: true, history }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The AI request failed. Please try again.');
    return result;
  };

  const appendFeedback = (feedback, number, target) => {
    const empty = target.querySelector('.mock-feedback-empty');
    if (empty) empty.remove();
    const item = document.createElement('article');
    item.className = 'mock-feedback-item';
    item.dataset.questionNumber = number;
    const heading = document.createElement('div');
    heading.className = 'mock-feedback-item-heading';
    const title = document.createElement('h3');
    title.textContent = `Question ${number}`;
    const score = document.createElement('span');
    score.className = 'mock-score';
    score.textContent = `${feedback.score}/5`;
    heading.append(title, score);
    item.append(heading);
    if (feedback.question) {
      const question = document.createElement('p');
      question.className = 'mock-feedback-question';
      question.textContent = feedback.question;
      item.append(question);
    }
    [
      ['Answer', feedback.answer_feedback],
      ['Camera', feedback.camera_feedback],
      ['Screen', feedback.screen_feedback],
    ].forEach(([label, text]) => {
      const section = document.createElement('section');
      const labelElement = document.createElement('strong');
      labelElement.textContent = label;
      const content = document.createElement('p');
      content.textContent = text;
      section.append(labelElement, content);
      item.append(section);
    });
    target.append(item);
  };

  const finishSession = message => {
    stopCapture();
    clearRecording();
    interview.querySelector('[data-capture-grid]').hidden = true;
    answerForm.hidden = true;
    if (pythonWorker) {
      pythonWorker.terminate();
      pythonWorker = null;
    }
    interview.querySelector('[data-session-finished]').hidden = false;
    interview.querySelector('[data-question-state]').textContent = 'Complete';
    setStatus(sessionStatus, message);
  };

  const PYODIDE_URL = 'https://cdn.jsdelivr.net/pyodide/v0.26.4/full/pyodide.js';
  const SQLJS_BASE = 'https://cdnjs.cloudflare.com/ajax/libs/sql.js/1.10.3/';
  const WORKER_SOURCE = `
    importScripts('${PYODIDE_URL}');
    let py;
    self.onmessage = async event => {
      let out = '';
      try {
        if (!py) py = await loadPyodide();
        py.setStdout({ batched: line => { out += line + '\\n'; } });
        py.setStderr({ batched: line => { out += line + '\\n'; } });
        await py.runPythonAsync(event.data.code, { globals: py.globals.get('dict')() });
        self.postMessage({ output: out });
      } catch (error) {
        self.postMessage({ output: out + String(error.message || error), failed: true });
      }
    };
  `;

  const loadScript = src => new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.addEventListener('load', resolve);
    script.addEventListener('error', () => reject(new Error('Could not load the code runner. Check your connection.')));
    document.head.append(script);
  });

  const runPython = code => new Promise(resolve => {
    if (!pythonWorker) {
      pythonWorker = new Worker(URL.createObjectURL(new Blob([WORKER_SOURCE], { type: 'text/javascript' })));
    }
    const worker = pythonWorker;
    const timer = window.setTimeout(() => {
      worker.terminate();
      if (pythonWorker === worker) pythonWorker = null;
      resolve('Stopped: the code took longer than 30 seconds to run.');
    }, 30000);
    worker.onmessage = event => {
      window.clearTimeout(timer);
      resolve(event.data.output || '(no output)');
    };
    worker.onerror = () => {
      window.clearTimeout(timer);
      worker.terminate();
      if (pythonWorker === worker) pythonWorker = null;
      resolve('The Python runner failed to start. Check your connection and try again.');
    };
    worker.postMessage({ code });
  });

  const runSql = async code => {
    if (!sqlEngine) {
      await loadScript(`${SQLJS_BASE}sql-wasm.js`);
      sqlEngine = await window.initSqlJs({ locateFile: file => `${SQLJS_BASE}${file}` });
    }
    const db = new sqlEngine.Database();
    try {
      const results = db.exec(code);
      if (!results.length) return 'Statements ran successfully (no rows returned).';
      return results.map(table => [
        table.columns.join(' | '),
        ...table.values.map(row => row.map(value => (value === null ? 'NULL' : value)).join(' | ')),
      ].join('\n')).join('\n\n');
    } catch (error) {
      return `Error: ${error.message}`;
    } finally {
      db.close();
    }
  };

  const updateCodeNote = () => {
    const language = codeLanguage.value;
    codeRunButton.disabled = language === 'pyspark';
    codeNote.textContent = {
      python: 'Runs in your browser (first run takes a few seconds to load). Standard library only.',
      sql: 'Runs on a fresh in-browser SQLite database each time, so keep the CREATE TABLE and INSERT statements in your code.',
      pyspark: 'PySpark cannot run in the browser. Write your solution and submit it for review.',
    }[language];
  };

  const showQuestion = number => {
    const data = questions[number - 1];
    currentQuestionData = data;
    currentQuestion = data.text;
    questionNumberLabel.textContent = number;
    questionText.textContent = data.text;
    const isCoding = data.type === 'coding';
    codePanel.hidden = !isCoding;
    voicePanel.hidden = isCoding;
    if (isCoding) {
      codeLanguage.value = data.language || 'python';
      codeEditor.value = data.starter || '';
      codeOutput.textContent = 'Output will appear here.';
      lastRunOutput = '';
      updateCodeNote();
    }
  };

  codeLanguage.addEventListener('change', updateCodeNote);

  const blockEditorPaste = event => {
    event.preventDefault();
    setStatus(sessionStatus, 'Pasting is disabled in the code editor. Type your solution.', true);
  };
  codeEditor.addEventListener('paste', blockEditorPaste);
  codeEditor.addEventListener('drop', blockEditorPaste);
  codeEditor.addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    event.preventDefault();
    const { selectionStart, selectionEnd, value } = codeEditor;
    codeEditor.value = `${value.slice(0, selectionStart)}    ${value.slice(selectionEnd)}`;
    codeEditor.selectionStart = codeEditor.selectionEnd = selectionStart + 4;
  });

  codeRunButton.addEventListener('click', async () => {
    const code = codeEditor.value;
    if (!code.trim()) {
      codeOutput.textContent = 'Write some code first.';
      return;
    }
    codeRunButton.disabled = true;
    codeOutput.textContent = 'Running...';
    try {
      lastRunOutput = codeLanguage.value === 'sql' ? await runSql(code) : await runPython(code);
    } catch (error) {
      lastRunOutput = error.message || 'The code could not be run.';
    }
    codeOutput.textContent = lastRunOutput;
    updateCodeNote();
  });

  topicSelect.addEventListener('change', () => {
    const isOther = topicSelect.value === '__other__';
    otherTopic.hidden = !isOther;
    otherTopic.required = isOther;
  });

  const wait = milliseconds => new Promise(resolve => window.setTimeout(resolve, milliseconds));

  const renderSessionResults = result => {
    resultsList.replaceChildren();
    feedbackList.replaceChildren();
    result.scores.forEach(score => {
      if (score.status === 'complete') {
        appendFeedback(score, score.question_number, resultsList);
        appendFeedback(score, score.question_number, feedbackList);
      } else if (score.status === 'failed') {
        const failed = document.createElement('p');
        failed.className = 'mock-feedback-empty is-error';
        failed.textContent = `Question ${score.question_number}: ${score.answer_feedback || 'Feedback could not be loaded.'}`;
        resultsList.append(failed);
      }
    });
    if (result.ready && result.session_rating !== null) {
      updateSessionRating(result.session_rating);
      resultsRating.textContent = `Overall rating ${result.session_rating.toFixed(1)}/5`;
      resultsRating.hidden = false;
    }
    if (result.pending_count) {
      resultsStatus.textContent = `Results received for ${result.finished_answers} of ${result.expected_answers} answers. Loading the rest...`;
    } else if (result.failed_answers) {
      resultsStatus.textContent = `${result.failed_answers} answer${result.failed_answers === 1 ? '' : 's'} could not be scored. The rating uses completed answers.`;
    } else {
      resultsStatus.textContent = `Results loaded for ${result.finished_answers} answers.`;
    }
  };

  const endSession = async message => {
    if (!sessionId || ending) return;
    ending = true;
    const endedSessionId = sessionId;
    const endedFeedbackRequests = [...pendingFeedback];
    const endedFailedNumbers = failedQuestionNumbers;
    const restartButton = interview.querySelector('[data-restart-session]');
    restartButton.disabled = true;
    finishSession(message);
    resultsTitle.textContent = 'Your results are loading';
    resultsStatus.textContent = 'Your recordings have been sent. Feedback and your rating will appear here as they finish.';
    resultsRating.hidden = true;
    resultsList.replaceChildren();
    resultsDialog.showModal();

    try {
      await requestAI({
        action: 'finish',
        session_id: endedSessionId,
        expected_answers: submittedAnswers,
      });
      let feedbackRequestsSettled = false;
      Promise.all(endedFeedbackRequests).then(() => {
        feedbackRequestsSettled = true;
      });
      const timeoutAt = Date.now() + 180000;
      while (Date.now() < timeoutAt) {
        const result = await requestAI({
          action: 'results',
          session_id: endedSessionId,
          failed_question_numbers: feedbackRequestsSettled ? endedFailedNumbers : [],
        });
        renderSessionResults(result);
        if (result.ready) {
          resultsTitle.textContent = 'Interview results';
          restartButton.disabled = false;
          return;
        }
        await wait(1500);
      }
      resultsStatus.textContent = 'Some feedback is taking longer than expected. Reload this page later to view saved results.';
    } catch (error) {
      resultsTitle.textContent = 'Results could not be loaded';
      resultsStatus.textContent = error.message || 'Please try again later.';
      restartButton.disabled = false;
    }
  };

  recordStartButton.addEventListener('click', () => {
    const audioTrack = cameraStream?.getAudioTracks()[0];
    if (!audioTrack || !window.MediaRecorder) {
      setStatus(recordingStatus, 'Audio recording is not supported by this browser.', true);
      return;
    }
    clearRecording();
    recordingChunks = [];
    const recordingStream = new MediaStream([audioTrack]);
    const mimeType = [
      'audio/webm;codecs=opus',
      'audio/mp4',
      'audio/webm',
      'audio/ogg;codecs=opus',
    ].find(type => MediaRecorder.isTypeSupported(type));
    const options = { audioBitsPerSecond: 32000 };
    if (mimeType) options.mimeType = mimeType;
    try {
      recorder = new MediaRecorder(recordingStream, options);
      recorder.addEventListener('dataavailable', event => {
        if (event.data.size) recordingChunks.push(event.data);
      });
      recorder.addEventListener('stop', () => {
        if (recordingTimer) window.clearInterval(recordingTimer);
        recordStopButton.disabled = true;
        recordStartButton.disabled = false;
        if (ending) {
          recordingChunks = [];
          return;
        }
        recordingBlob = new Blob(recordingChunks, { type: recorder.mimeType || 'audio/webm' });
        if (!recordingBlob.size) {
          setStatus(recordingStatus, 'No audio was captured. Try recording again.', true);
          return;
        }
        if (recordingBlob.size > 450000) {
          recordingBlob = null;
          setStatus(recordingStatus, 'That recording is too large. Try a shorter answer.', true);
          return;
        }
        recordingUrl = URL.createObjectURL(recordingBlob);
        recordingPreview.src = recordingUrl;
        recordingPreview.hidden = false;
        answerButton.disabled = false;
        setStatus(recordingStatus, 'Recording ready. Listen before sending.');
      });
      recorder.start(1000);
      recordingSeconds = 0;
      recordStartButton.disabled = true;
      recordStopButton.disabled = false;
      answerButton.disabled = true;
      setStatus(recordingStatus, 'Recording 00:00 / 01:00');
      recordingTimer = window.setInterval(() => {
        recordingSeconds += 1;
        if (recordingSeconds >= 60) {
          recorder.stop();
          return;
        }
        const minutes = String(Math.floor(recordingSeconds / 60)).padStart(2, '0');
        const seconds = String(recordingSeconds % 60).padStart(2, '0');
        setStatus(recordingStatus, `Recording ${minutes}:${seconds} / 01:00`);
      }, 1000);
    } catch (error) {
      recordStartButton.disabled = false;
      recordStopButton.disabled = true;
      setStatus(recordingStatus, error.message || 'Could not start audio recording.', true);
    }
  });

  recordStopButton.addEventListener('click', () => {
    if (recorder?.state === 'recording') recorder.stop();
  });

  startForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    if (!navigator.mediaDevices?.getUserMedia || !navigator.mediaDevices?.getDisplayMedia || !window.MediaRecorder) {
      setStatus(setupStatus, 'This browser does not support camera, screen sharing, and audio recording.', true);
      return;
    }
    role = (topicSelect.value === '__other__' ? otherTopic.value : topicSelect.value).trim();
    difficulty = startForm.elements.difficulty ? startForm.elements.difficulty.value : 'medium';
    if (!role || !startForm.elements.consent.checked) return;
    busy = true;
    startForm.querySelector('button[type="submit"]').disabled = true;
    setStatus(setupStatus, 'Requesting camera, microphone, and screen access...');
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      cameraPreview.srcObject = cameraStream;
      await cameraPreview.play();
      screenStream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 5 }, audio: false });
      screenPreview.srcObject = screenStream;
      await screenPreview.play();
      setup.hidden = true;
      session.hidden = false;
      questionNumber = 1;
      sessionId = null;
      history = [];
      sessionRating.hidden = true;
      feedbackList.replaceChildren(Object.assign(document.createElement('p'), {
        className: 'mock-feedback-empty',
        textContent: 'Feedback for each answer will appear here.',
      }));
      const result = await requestAI({ action: 'question' });
      questions = result.questions;
      sessionId = result.session_id;
      showQuestion(1);
      pendingFeedback = [];
      failedQuestionNumbers = [];
      submittedAnswers = 0;
      ending = false;
      setStatus(sessionStatus, 'Camera and screen are live. Ten questions are ready.');
      screenStream.getVideoTracks()[0].addEventListener('ended', () => endSession('Screen sharing stopped. Session ended.'));
    } catch (error) {
      stopCapture();
      session.hidden = true;
      setup.hidden = false;
      startForm.querySelector('button[type="submit"]').disabled = false;
      setStatus(setupStatus, error.message || 'Camera and screen access are required to start.', true);
    } finally {
      busy = false;
    }
  });

  answerForm.addEventListener('submit', async event => {
    event.preventDefault();
    const isCoding = currentQuestionData?.type === 'coding';
    if (busy || ending || !currentQuestion || !sessionId || (!isCoding && !recordingBlob)) return;
    if (isCoding && !codeEditor.value.trim()) {
      setStatus(sessionStatus, 'Write your code before submitting.', true);
      return;
    }
    busy = true;
    answerButton.disabled = true;
    codeSubmitButton.disabled = true;
    try {
      const answerPayload = isCoding
        ? { code: codeEditor.value, language: codeLanguage.value, code_output: lastRunOutput }
        : { audio: await blobToDataUrl(recordingBlob) };
      const questionBeingAnswered = questionNumber;
      const questionFailureList = failedQuestionNumbers;
      const feedbackRequest = requestAI({
        action: 'feedback',
        session_id: sessionId,
        question_number: questionBeingAnswered,
        question: currentQuestion,
        ...answerPayload,
        frames: { camera: captureFrame(cameraPreview), screen: captureFrame(screenPreview) },
      }).then(() => null).catch(error => {
        questionFailureList.push(questionBeingAnswered);
        return error;
      });
      pendingFeedback.push(feedbackRequest);
      submittedAnswers += 1;
      clearRecording();
      if (questionBeingAnswered >= maxQuestions) {
        await endSession('All ten answers are submitted. Results are loading.');
      } else {
        questionNumber += 1;
        showQuestion(questionNumber);
        setStatus(sessionStatus, 'Answer sent. Continue when you are ready; feedback will load at the end.');
      }
    } catch (error) {
      setStatus(sessionStatus, error.message || 'Could not get feedback. Please try again.', true);
      answerButton.disabled = !recordingBlob;
    } finally {
      codeSubmitButton.disabled = false;
      busy = false;
    }
  });

  interview.querySelector('[data-end-session]').addEventListener('click', () => {
    if (!busy) endSession('Session ended. Your results are loading.');
  });

  interview.querySelector('[data-results-close]').addEventListener('click', () => {
    resultsDialog.close();
  });

  interview.querySelector('[data-restart-session]').addEventListener('click', () => {
    stopCapture();
    clearRecording();
    history = [];
    sessionId = null;
    questions = [];
    pendingFeedback = [];
    failedQuestionNumbers = [];
    submittedAnswers = 0;
    questionNumber = 1;
    currentQuestion = '';
    currentQuestionData = null;
    ending = false;
    sessionRating.hidden = true;
    answerForm.hidden = false;
    interview.querySelector('[data-session-finished]').hidden = true;
    interview.querySelector('[data-capture-grid]').hidden = false;
    session.hidden = true;
    setup.hidden = false;
    startForm.querySelector('button[type="submit"]').disabled = false;
    setStatus(setupStatus, '');
  });
})();

(function () {
  var url = window.SIGNUP_POLL_URL;
  if (!url) return;
  var baseTitle = document.title.replace(/^\(\d+\)\s*/, '');
  var badge = document.querySelector('[data-signup-badge]');
  var last = null;
  function render(count) {
    document.title = (count > 0 ? '(' + count + ') ' : '') + baseTitle;
    if (badge) { badge.textContent = count; badge.hidden = count <= 0; }
  }
  function poll() {
    fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) {
        if (!d) return;
        if (last !== null && d.count > last && 'Notification' in window && Notification.permission === 'granted') {
          new Notification('New sign-up request', { body: d.count + ' request(s) awaiting approval' });
        }
        last = d.count;
        render(d.count);
      })
      .catch(function () {});
  }
  if ('Notification' in window && Notification.permission === 'default') Notification.requestPermission();
  poll();
  setInterval(poll, 20000);
})();

(function () {
  const interview = document.querySelector('[data-mock-interview]');
  if (!interview) return;
  const url = interview.dataset.aiUrl;
  const csrf = (document.querySelector('[name=csrfmiddlewaretoken]') || {}).value
    || (document.cookie.match(/csrftoken=([^;]+)/) || [])[1] || '';
  const warning = interview.querySelector('[data-integrity-warning]');
  const getState = () => window.__mockIntegrity && window.__mockIntegrity();
  let leftAt = null;
  let count = 0;

  const report = (type, detail) => {
    const state = getState();
    if (!state || !state.sessionId) return;
    count += 1;
    if (warning) {
      warning.hidden = false;
      warning.textContent = `Activity flagged (${count}): leaving the interview tab, pasting or copying is recorded and shown to your trainer.`;
    }
    fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
      body: JSON.stringify({
        action: 'integrity', consent: true, session_id: state.sessionId,
        events: [{ type, detail: detail || '', question: state.question }],
      }),
    }).then(r => r.json()).then(result => {
      if (result && result.terminate && window.__mockEnd) {
        window.__mockEnd('Session ended automatically: too many suspicious activities were flagged.');
      }
    }).catch(() => {});
  };

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) { leftAt = Date.now(); report('tab_hidden'); }
    else if (leftAt) { leftAt = null; }
  });
  window.addEventListener('blur', () => {
    setTimeout(() => { if (!document.hidden) report('window_blur'); }, 400);
  });
  document.addEventListener('paste', event => {
    const text = (event.clipboardData && event.clipboardData.getData('text')) || '';
    report('paste', `${text.length} chars`);
  });
  document.addEventListener('copy', () => report('copy'));
  document.addEventListener('contextmenu', () => report('context_menu'));
  document.addEventListener('keydown', event => {
    const k = event.key.toLowerCase();
    if (event.key === 'F12' || ((event.ctrlKey || event.metaKey) && event.shiftKey && ['i', 'j', 'c'].includes(k))
      || ((event.ctrlKey || event.metaKey) && k === 'u')) report('devtools_key', event.key);
  });
})();
