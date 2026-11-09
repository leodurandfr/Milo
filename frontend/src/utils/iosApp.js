// frontend/src/utils/iosApp.js
/**
 * Whether this page is shown inside the Milo-iOS app rather than a browser.
 *
 * The app's web view replaces the whole user agent with its own
 * (`webView.customUserAgent = "Milo-iOS-App/1.0"`, ViewController.swift), set
 * before the first load, so the answer holds from the first byte and through
 * every reload — unlike the `ios-app` class the app adds to <body> only after
 * its launch probe answers. Matched on the prefix: the version may move.
 */
export function isMiloIosApp() {
  return navigator.userAgent.startsWith('Milo-iOS-App/');
}
