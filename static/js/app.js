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
  const csrfToken = interview.querySelector('[name="csrfmiddlewaretoken"]').value;
  const maxQuestions = 5;
  let cameraStream;
  let screenStream;
  let recorder;
  let recordingChunks = [];
  let recordingBlob;
  let recordingUrl;
  let recordingTimer;
  let recordingSeconds = 0;
  let role = '';
  let sessionId = null;
  let questionNumber = 1;
  let currentQuestion = '';
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
      body: JSON.stringify({ ...payload, role, consent: true, history }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The AI request failed. Please try again.');
    return result;
  };

  const appendFeedback = feedback => {
    const empty = feedbackList.querySelector('.mock-feedback-empty');
    if (empty) empty.remove();
    const item = document.createElement('article');
    item.className = 'mock-feedback-item';
    const heading = document.createElement('div');
    heading.className = 'mock-feedback-item-heading';
    const title = document.createElement('h3');
    title.textContent = `Question ${questionNumber}`;
    const score = document.createElement('span');
    score.className = 'mock-score';
    score.textContent = `${feedback.score}/5`;
    heading.append(title, score);
    item.append(heading);
    [
      ['Transcript', feedback.answer_transcript || 'No transcript returned.'],
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
    feedbackList.prepend(item);
  };

  const finishSession = message => {
    stopCapture();
    clearRecording();
    interview.querySelector('[data-capture-grid]').hidden = true;
    answerForm.hidden = true;
    interview.querySelector('[data-session-finished]').hidden = false;
    interview.querySelector('[data-question-state]').textContent = 'Complete';
    setStatus(sessionStatus, message);
  };

  const endSession = async message => {
    if (sessionId) {
      try {
        const result = await requestAI({ action: 'finish', session_id: sessionId });
        updateSessionRating(result.session_rating);
      } catch (error) {
        setStatus(sessionStatus, error.message || 'Could not save the session status.', true);
      }
    }
    finishSession(message);
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
    role = startForm.elements.role.value.trim();
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
      const result = await requestAI({ action: 'question', question_number: questionNumber });
      currentQuestion = result.question;
      sessionId = result.session_id;
      questionText.textContent = currentQuestion;
      questionNumberLabel.textContent = questionNumber;
      setStatus(sessionStatus, 'Camera and screen are live. Record your answer when you are ready.');
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
    if (busy || !recordingBlob || !currentQuestion || !sessionId) return;
    busy = true;
    answerButton.disabled = true;
    setStatus(sessionStatus, 'Sending your recording and snapshots for feedback...');
    try {
      const audio = await blobToDataUrl(recordingBlob);
      const feedback = await requestAI({
        action: 'feedback',
        session_id: sessionId,
        question_number: questionNumber,
        question: currentQuestion,
        audio,
        frames: { camera: captureFrame(cameraPreview), screen: captureFrame(screenPreview) },
      });
      appendFeedback(feedback);
      history.push({ question: currentQuestion, answer: feedback.answer_transcript || '' });
      updateSessionRating(feedback.session_rating);
      clearRecording();
      if (questionNumber >= maxQuestions || !feedback.next_question) {
        finishSession(`Interview complete. Your saved overall rating is ${feedback.session_rating.toFixed(1)}/5.`);
      } else {
        questionNumber += 1;
        currentQuestion = feedback.next_question;
        questionNumberLabel.textContent = questionNumber;
        questionText.textContent = currentQuestion;
        setStatus(sessionStatus, 'Feedback received. Your next question is ready.');
      }
    } catch (error) {
      setStatus(sessionStatus, error.message || 'Could not get feedback. Please try again.', true);
      answerButton.disabled = !recordingBlob;
    } finally {
      busy = false;
    }
  });

  interview.querySelector('[data-end-session]').addEventListener('click', () => {
    if (!busy) endSession('Session ended. Your rating and question scores are saved.');
  });

  interview.querySelector('[data-restart-session]').addEventListener('click', () => {
    stopCapture();
    clearRecording();
    history = [];
    sessionId = null;
    questionNumber = 1;
    currentQuestion = '';
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
