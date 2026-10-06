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
  const answerInput = interview.querySelector('[name="answer"]');
  const answerButton = interview.querySelector('[data-answer-submit]');
  const sessionStatus = interview.querySelector('[data-session-status]');
  const setupStatus = interview.querySelector('[data-interview-status]');
  const feedbackList = interview.querySelector('[data-feedback-list]');
  const csrfToken = interview.querySelector('[name="csrfmiddlewaretoken"]').value;
  const maxQuestions = 5;
  let cameraStream;
  let screenStream;
  let role = '';
  let questionNumber = 1;
  let currentQuestion = '';
  let history = [];
  let busy = false;

  const setStatus = (element, message, isError = false) => {
    element.textContent = message;
    element.classList.toggle('is-error', isError);
  };

  const stopCapture = () => {
    [cameraStream, screenStream].forEach(stream => {
      if (stream) stream.getTracks().forEach(track => track.stop());
    });
    cameraStream = null;
    screenStream = null;
    cameraPreview.srcObject = null;
    screenPreview.srcObject = null;
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
    interview.querySelector('[data-capture-grid]').hidden = true;
    answerForm.hidden = true;
    interview.querySelector('[data-session-finished]').hidden = false;
    interview.querySelector('[data-question-state]').textContent = 'Complete';
    setStatus(sessionStatus, message);
  };

  startForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    if (!navigator.mediaDevices?.getUserMedia || !navigator.mediaDevices?.getDisplayMedia) {
      setStatus(setupStatus, 'This browser does not support camera and screen sharing.', true);
      return;
    }
    role = startForm.elements.role.value.trim();
    if (!role || !startForm.elements.consent.checked) return;
    busy = true;
    startForm.querySelector('button[type="submit"]').disabled = true;
    setStatus(setupStatus, 'Requesting camera and screen access...');
    try {
      cameraStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      cameraPreview.srcObject = cameraStream;
      await cameraPreview.play();
      screenStream = await navigator.mediaDevices.getDisplayMedia({ video: { frameRate: 5 }, audio: false });
      screenPreview.srcObject = screenStream;
      await screenPreview.play();
      setup.hidden = true;
      session.hidden = false;
      questionNumber = 1;
      history = [];
      feedbackList.replaceChildren(Object.assign(document.createElement('p'), {
        className: 'mock-feedback-empty',
        textContent: 'Feedback for each answer will appear here.',
      }));
      const result = await requestAI({ action: 'question', question_number: questionNumber });
      currentQuestion = result.question;
      questionText.textContent = currentQuestion;
      questionNumberLabel.textContent = questionNumber;
      answerButton.disabled = false;
      setStatus(sessionStatus, 'Your camera and screen are live. No video is being recorded or saved.');
      screenStream.getVideoTracks()[0].addEventListener('ended', () => finishSession('Screen sharing stopped. Session ended.'));
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
    const answer = answerInput.value.trim();
    if (busy || !answer || !currentQuestion) return;
    busy = true;
    answerButton.disabled = true;
    setStatus(sessionStatus, 'Sending snapshots and answer for feedback...');
    try {
      const feedback = await requestAI({
        action: 'feedback',
        question_number: questionNumber,
        question: currentQuestion,
        answer,
        frames: { camera: captureFrame(cameraPreview), screen: captureFrame(screenPreview) },
      });
      appendFeedback(feedback);
      history.push({ question: currentQuestion, answer });
      answerInput.value = '';
      if (questionNumber >= maxQuestions || !feedback.next_question) {
        finishSession('Your session is complete. Feedback is available here until you leave this page.');
      } else {
        questionNumber += 1;
        currentQuestion = feedback.next_question;
        questionNumberLabel.textContent = questionNumber;
        questionText.textContent = currentQuestion;
        answerButton.disabled = false;
        setStatus(sessionStatus, 'Feedback received. Your next question is ready.');
      }
    } catch (error) {
      setStatus(sessionStatus, error.message || 'Could not get feedback. Please try again.', true);
      answerButton.disabled = false;
    } finally {
      busy = false;
    }
  });

  interview.querySelector('[data-end-session]').addEventListener('click', () => {
    finishSession('Session ended. Your answers and snapshots were not saved by the app.');
  });

  interview.querySelector('[data-restart-session]').addEventListener('click', () => {
    stopCapture();
    history = [];
    questionNumber = 1;
    currentQuestion = '';
    answerForm.hidden = false;
    interview.querySelector('[data-session-finished]').hidden = true;
    interview.querySelector('[data-capture-grid]').hidden = false;
    session.hidden = true;
    setup.hidden = false;
    startForm.querySelector('button[type="submit"]').disabled = false;
    setStatus(setupStatus, '');
  });
})();
