// Shows Firebase push alerts while the app is closed. Tapping one opens the
// stock's page (the link is set by run_daily.py).
importScripts("https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.14.1/firebase-messaging-compat.js");
importScripts("firebase-config.js");

if (self.FIREBASE_CONFIG && self.FIREBASE_CONFIG.apiKey) {
  firebase.initializeApp(self.FIREBASE_CONFIG);
  firebase.messaging();
}
