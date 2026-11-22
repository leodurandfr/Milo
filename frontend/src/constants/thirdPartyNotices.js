// frontend/src/constants/thirdPartyNotices.js
//
// Where the built interface serves the repository's THIRD_PARTY_NOTICES.md:
// the Vite build emits it there (vite-plugins/thirdPartyNotices.js) and
// Settings › System › Third-party licenses reads it from there. Two consumers,
// one spelling: a rename on one side only is a licenses page showing the SPA's
// index.html, which nginx answers for any path it does not find.
export const THIRD_PARTY_NOTICES_PATH = '/THIRD_PARTY_NOTICES.md';
