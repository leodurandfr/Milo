# Refonte du système hauteur / scroll / transition de la modale

> Doc de conception à **verrouiller avant tout code**. Référence d'implémentation : on suit cette spec, on ne l'invente pas.
> Cible : appliance figée (PWA iOS WebKit + kiosk Pi à thread principal lent). Frontend-only, zéro impact REST/WS/contrat Milo-Mac.

---

## Décisions actées (priment sur la prose ci-dessous)

- **D1 — Header : DÉFILANT (reste dans le scroller).** La proposition « header fixe / sorti du scroller » du §3 est **annulée**. Partout où le doc dit « header fixe » ou « header hors du scroller », lire désormais : *le `NavigationHeader` reste à l'intérieur du scroller et défile avec le contenu (comportement actuel inchangé)*. Le gain visé (suppression du clone de header) est **conservé par un autre chemin** : sur une navigation scrollée, un `transform: translateY(-scrollTop)` fige la **vue sortante** le temps du cross-fade pendant qu'on repositionne `scrollTop`, et on s'appuie sur le **`header-fade` interne** de `NavigationHeader` (grid-stack déjà présent, NavigationHeader.vue:115-127) pour le cross-fade du titre. Aucun `cloneNode`. Seul cas à valider sur device : **back-restore vers une position scrollée**. Cf. §7.1 (résolue) et la décision ouverte #3 (qui devient le mécanisme retenu).
  - **Affinage (post-rewrite) — fade du *bar* de header sur nav scrollée.** Comme le header défile, une nav qui remet `scrollTop` au-delà de sa hauteur fait *apparaître/disparaître la barre entière en une frame* (pop), alors que seul son *titre* cross-fade (`header-fade` interne). `useViewTransition` accepte donc un `headerRef` optionnel et **fade la barre** : forward-depuis-scrollé → fade-in (opacity 0→1, header déjà à sa position de repos) ; back-vers-scrollé → la barre est **tenue en place par un `transform: translate3d(0,T,0)`** (contre le scroll) et fade-out, puis relâchée en `onAfterLeave` une fois hors-écran. `transform` (libre sur le header, hors fade-slide) sert aussi de couche GPU pour une opacité fluide sur iOS. Câblé sur SettingsModal + EqualizerModal ; AudioSourceLayout ne passe pas `headerRef` (inchangé).
- Le **cœur du redesign** (`.modal-clip` qui porte la height animée + `.modal-scroller` à hauteur explicite-finale) est **indépendant de D1** et reste tel quel.

---

## 1. Objectif & non-objectifs

### Objectif
Réécrire **de zéro** le couplage hauteur/scroll/transition des modales `Settings` et `Equalizer`, dont la cause racine est unique :

> Aujourd'hui le **même nœud DOM** (`.modal-content`, Modal.vue:15) est À LA FOIS le scroller (`overflow-y:auto`, Modal.vue:538) ET l'enfant direct du conteneur dont la `height` est animée par un ressort (`.modal-container`, `transition: height var(--transition-spring-slow)`, Modal.vue:504). Pendant l'overshoot du ressort (la courbe `linear()` de `--transition-spring-slow` culmine à **1.0315**, design-system.css:145), `modal-content.clientHeight ≥ scrollHeight` ⇒ `maxScroll = 0` ⇒ toute écriture de `scrollTop` est **silencieusement clampée à 0**.

Toute la cicatrice (deferScrollRestore, compteur de génération, double-rAF, `.overflow-transitioning`, height lock, skipUnlockCorrection, clone de header, CSS-offset, pinnedWrapper, savedInnerHeight) n'existe que pour survivre à ce conflit — et **gèle le scroll ~1,6 s après chaque navigation**, voire pour toujours si `transitionend(height)` ne se déclenche jamais.

**But du rewrite : un scroll qui n'est JAMAIS gelé, et un `scrollTop` qui n'a JAMAIS besoin d'être différé.** Invariant central : `maxScroll` du scroller doit déjà être correct à l'instant exact où `scrollTop` est écrit.

### Non-objectifs (on ne touche pas)
- **Aucun changement de surface REST/WS/Milo-Mac.** 100 % frontend (composants Vue + composables + CSS).
- **AudioSourceLayout** (consommateur non-modal de `useViewTransition`, AudioSourceLayout.vue:145-150) : scroller à hauteur fixe, ne passe **aucun** callback Modal (`requestHeightDelta`/`deferScrollRestore`/`cancelDeferred`). Son fade de gradient (AudioSourceLayout.vue:156-205) et son `prepareNavigation` via `onBeforeUpdate` (AudioSourceLayout.vue:210-213) doivent continuer **inchangés**. La séparation « machinerie Modal opt-in via callbacks » est conservée.
- **NavigationHeader** : son cross-fade interne de titre (grid-stack + `<Transition name="header-fade">`, NavigationHeader.vue:9-33, 115-127, 198-209) existe déjà et fonctionne sur iOS (workarounds `translate3d`/`backface-visibility` lignes 200-208). On le **réutilise tel quel** ; il **reste dans le scroller** (D1, header défilant). On ne le déplace pas — le clone est supprimé via le `transform` sur la vue sortante (D1).
- **API publique injectée `requestHeightDelta`** : utilisée HORS navigation par `ToggleSection.vue:43,85`, `MultiroomControl.vue:47,352,359`, `NetworkSettings.vue:154,316-360` pour les morphs de hauteur intra-vue (accordéons). **Elle reste**, contrat inchangé pour ces appelants (voir §3 « ce qu'on garde »). C'est le piège n°1 que les juges ont relevé sur les designs qui la supprimaient.
- Pas de keep-alive des ~25 vues (raisons en §4).
- Timings/feel du cross-fade (out-then-in : leave `--transition-fast-leave` 200 ms + enter `transition-delay:100ms`, SettingsModal.vue:692-702) : **conservés à l'identique**.
- L'animation open/close de la modale (Modal.vue:272-388), le verrou body-scroll (Modal.vue:409-415,459), l'auto-close 120 s (Modal.vue:231-238).

---

## 2. Spec verrouillée (Phase 0) — checklist d'acceptation / non-régression

### 2.1 Invariants (doivent rester vrais après le rewrite)

| # | Invariant | Ancrage code actuel |
|---|---|---|
| I1 | **Un seul scroller visible** par l'utilisateur. | `.modal-content` overflow-y:auto Modal.vue:538 |
| I2 | La hauteur visible suit le contenu via un ressort CSS, bornée à `getMaxHeight()`. | Modal.vue:504, useAnimatedHeight.js:46-86, Modal.vue:64-71 |
| I3 | Hauteur ≤ `overlay.clientHeight - padTop - padBottom - 24px`. Au-delà : modale à max, le scroller scrolle. | Modal.vue:64-71 |
| I4 | `scrollTop` survit à la navigation : push sauve, back restaure, forward remet à 0. | useNavigationStack.js:36-54 |
| I5 | **L'écriture de `scrollTop` atterrit, jamais clampée à 0** — sans geler le scroll. | nouvel invariant central |
| I6 | Une seule vue montée à la fois (pas de keep-alive). | SettingsModal.vue:29-236 |
| I7 | Cross-fade out-then-in, pas de slide ; vue sortante hors-flux pendant le fade. | SettingsModal.vue:692-702 |
| I8 | Un seul header persistant ; état final = un header, zéro style inline, zéro clone orphelin. | SettingsModal.vue:5-25 |
| I9 | Body scroll verrouillé tant que la modale est ouverte. | Modal.vue:409-415,459 |
| I10 | Auto-close 120 s, reset sur pointerdown/wheel/touchstart, persiste à travers les navs. | Modal.vue:231-238,418-432 |
| I11 | **Fail-open** : un event de transition manqué ne doit JAMAIS laisser le scroll gelé pour toujours. | (bug actuel Modal.vue:90-97,200-209) |
| I12 | Tous les timers via `useTimer` (auto-cleanup), zéro `window.setTimeout` brut. | Modal.vue:215, useAnimatedHeight.js:33,156 |
| I13 | AudioSourceLayout (non-modal) ne régresse pas ; machinerie Modal opt-in via callbacks. | AudioSourceLayout.vue:145-150 |
| I14 | Zéro impact contrat (REST/WS/Milo-Mac). | — |
| I15 | `requestHeightDelta` reste fonctionnel pour les accordéons intra-vue. | ToggleSection.vue:85, MultiroomControl.vue:352, NetworkSettings.vue:316-360 |

### 2.2 Tableau des scénarios (comportement attendu)

| Scénario | Comportement attendu | Réf. actuelle |
|---|---|---|
| **Ouverture** | Overlay fade-in ; conteneur scale(0.95)+opacity 0 → scale(1) ; hauteur initialisée à la 1re vue **sans** animation (pas de ressort depuis 0) ; bouton close après ~500 ms ; scroll utilisable dès layout. | Modal.vue:272-342 |
| **Fermeture** | Conteneur shrink/scale+fade ~180 ms, overlay ~250 ms ; `isVisible=false` ; body-scroll libéré. | Modal.vue:344-388 |
| **Forward court→haut (fits)** | Ancienne vue fade-out, nouvelle fade-in ; hauteur ressort UP ; nouvelle vue à scrollTop=0 ; pas de gel, pas de double-spring. | useViewTransition.js:277-319 |
| **Forward haut→court (shrink)** | Hauteur ressort DOWN ; overflow disparaît ; nouvelle à scrollTop=0 ; **pas de crop** du contenu sortant pendant le fade. | useAnimatedHeight.js:112-128 |
| **Forward EN SCROLLÉ** | Nouvelle vue à scrollTop=0 ; **la vue sortante reste figée à son offset** pendant le fade (pas de flash jump-to-top) ; header cross-fade propre (pas de titre dupliqué/téléporté) ; si le header était scrollé hors-écran, **sa barre fade-in (pas de pop)**. | useViewTransition.js:153-191 |
| **Back AVEC restore (T>0)** | Vue d'arrivée affichée **déjà à l'offset T**, sans animation scroll-from-top ; header aligné ; si T dépasse la hauteur du header, **sa barre fade-out (tenue en place puis relâchée), pas de pop** ; après le fade `scrollTop == T` ; pendingScrollRestore vidé. **(le scénario raison-d'être de toute la cicatrice)** | useViewTransition.js:216-250, Modal.vue:155-212 |
| **Les deux vues overflow (max)** | La hauteur **n'anime pas** (déjà à max des deux côtés) ; seulement cross-fade + scroll ; pas de shrink-then-grow. | useViewTransition.js:298-319 |
| **Delta nul / <2px** | Aucune animation de hauteur ; cross-fade instantané ; pas de ressort sur jitter sous-pixel. | useViewTransition.js:313, useAnimatedHeight.js:77 |
| **Push/back/push rapides** | Chaque nav supersède proprement ; pas de target obsolète, pas de clone/élément absolu orphelin, pas de correction parasite ; état final = dernière nav. | useViewTransition.js:70-103, Modal.vue:136-211 |
| **Interruption mid-spring** | Le ressort en cours est repris par la nouvelle cible ; pas de finalize obsolète sur `transitioncancel` ; scroll non écrasé ; styles header reset. | Modal.vue:145-152, useViewTransition.js:90-103 |
| **Nav non-scrollée vers top** | Cross-fade + ressort simples ; pas de clone, pas de CSS-offset. | useViewTransition.js:192-205,266-272 |
| **EqualizerModal (3 états)** | disabled↔loading↔controls cross-fadent ; piloté par store, pas par push/back ; `prepareNavigation` avant le patch Vue (`flush:'pre'`). | EqualizerModal.vue:165-173 |
| **Croissance async post-nav** | Re-ressort vers la nouvelle hauteur ; clamp à max si overflow ; `scrollTop` courant préservé. | useAnimatedHeight.js:46-81 |
| **Accordéon intra-vue (ToggleSection/Multiroom/Network)** | Le conteneur ressorte en lock-step avec l'animation grid interne ; `requestHeightDelta` continue de fonctionner. | ToggleSection.vue:69-87 |

### 2.3 Contraintes issues de la cicatrice (pièges à NE PAS réintroduire)

> Chaque ligne = un mécanisme actuel + la garantie que le rewrite doit fournir À LA PLACE.

- **C1 — overshoot ⇒ clamp.** Ne jamais animer la hauteur **du nœud qui scrolle**. Le `clientHeight` du scroller doit être indépendant du ressort de hauteur. (remplace deferScrollRestore Modal.vue:130-212)
- **C2 — overflow toggle.** Préserver `scrollTop` à travers un changement de hauteur **sans jamais toucher `overflow`** du scroller. (remplace `.overflow-transitioning` + l'override inline `overflowY='auto'` Modal.vue:179,549-551)
- **C3 — async à annuler.** Rendre la finalisation de nav **synchrone** ⇒ rien à annuler, plus de compteur de génération ni de double-rAF. (remplace Modal.vue:136-211)
- **C4 — pas de dépendance aux events de transition** pour la correction du scroll. (remplace double-rAF + isHeightTransitioning Modal.vue:193-211)
- **C5 — une seule autorité de hauteur** à la fois, handoff propre, jamais deux ressorts en concurrence. (remplace height lock useAnimatedHeight.js:148-187)
- **C6 — une seule source de vérité de la hauteur cible par nav** (mesurée OU prédite, pas les deux en course). (remplace skipUnlockCorrection)
- **C7 — la boîte du cross-fade réserve `max(sortant, entrant)`** intrinsèquement, sans pin/unpin manuel. (remplace pinnedWrapper useViewTransition.js:145-151)
- **C8 — cross-fade du header en lock-step** sur nav scrollée, sans clone manuel. (remplace cloneNode useViewTransition.js:113-188)
- **C9 — `scrollTop` écrit immédiatement et correctement** ⇒ supprime le CSS-offset trick et le workaround savedInnerHeight. (remplace useViewTransition.js:219-298)
- **C10 — `getMaxHeight` overshoot-free dans le cas at-max** : le ressort ne doit pas pousser le clip au-delà du bord viewport ; le 24px ne doit plus être load-bearing pour la correction scroll (il reste comme respiration cosmétique). (remplace bounceMargin Modal.vue:69)
- **C11 — fail-open** : aucun état où un event manqué gèle le scroll (I11).

---

## 3. Architecture retenue — **Clip externe + scroller à hauteur explicite-finale** (« DécIle »)

**Gagnante : variante #2/#3 fusionnées (« Stable-Scroller / Morph-Mask » + « DécILe externe minimal »).** Score-juges le plus haut sur *simplicity-gain* (5/5) et *ios-webkit-risk* (4/5), avec un correctif explicite sur les 3 points où les juges les ont cassées.

### 3.1 Pourquoi celle-ci, et greffes des dauphines

On retient le **découplage du nœud qui morphe (`height` animée) du nœud qui scrolle**, mais **sans keep-alive** (contre l'archi #1 « per-view scroller + keep-alive », rejetée en §4). On greffe explicitement :

1. **De #1 (per-view scroller + keep-alive)** : on **ne garde PAS** le keep-alive (coût mémoire/timers sur Pi + clés composites non résolues pour `zoneGroupId`/`macIdToEdit`/`stationToEdit` qui sont des refs partagées, SettingsModal.vue:334-335,385). On garde **uniquement** son idée de **grid-stack des vues dans une seule cellule** : réserve `max(sortant,entrant)` intrinsèquement ⇒ tue `pinnedWrapper` (C7) sans importer le coût keep-alive.
2. **De #2 (Stable-Scroller)** : le cœur — **scroller à hauteur explicite px = cible finale, posée synchronement, jamais animée**. C'est ce qui rend `maxScroll` correct dès la frame 0 (C1, C5, C6).
3. **De #3 (DécILe)** : l'**insertion d'un seul nœud `.modal-clip`** (clip overflow:hidden qui porte la `height` animée) entre wrapper et scroller, plutôt que de réutiliser `.modal-container`. Plus propre pour ré-allouer radius/glass-stroke (Modal.vue:507-522).
4. **Suppression du clone, header GARDÉ défilant (D1).** Le header reste dans le flux du scroller (SettingsModal.vue:5, il scrolle — décision D1). On supprime quand même le clone : sur une nav scrollée, on fige la **vue sortante** par `transform: translateY(-scrollTop)` le temps du cross-fade (pendant qu'on écrit la cible `scrollTop`), et le **`header-fade` interne** de `NavigationHeader` (déjà présent) cross-fade le titre. Plus de `cloneNode`/insert/absolute (C8). Cas à border sur device : **back-restore scrollé** (le header de destination est légitimement hors-écran ; son fade-out doit rester propre).

### 3.2 Modèle DOM cible

```
.modal-overlay            (fixed, flex, body-scroll-lock — inchangé Modal.vue:470-485)
  .modal-wrapper          (inchangé)
    .close-btn-wrapper    (inchangé Modal.vue:5-8)
    .modal-shell          [NOUVEAU rôle, ex-.modal-container] overflow:hidden, radius, glass ::before,
                           opacity/scale pour open/close. NE scrolle pas. NE porte PAS de height animée.
                           display:flex; flex-direction:column.
      .modal-clip         [NOUVEAU] overflow:hidden ; PORTE la height animée
                           (transition: height var(--transition-spring-light)) ; flex:0 0 auto.
        .modal-scroller   [ex-.modal-content] overflow-y:auto ; touch-action:pan-y ; padding ;
                           height EXPLICITE en px (= cible finale, transition:none) ; JAMAIS overflow togglé.
          NavigationHeader [reste DANS le scroller — D1] persistant, au-dessus du stack ; défile avec le contenu.
          .view-stack     [NOUVEAU] display:grid ; une seule cellule (tous enfants grid-row:1/col:1).
                           Réserve max(sortant,entrant) intrinsèquement (remplace pinnedWrapper + leave-active absolute).
            <Transition>   vue sortante + entrante empilées dans la cellule (plus de position:absolute manuel).
              .view-content (slot)
```

### 3.3 Qui possède la hauteur vs le scroll

| Propriété | Nœud | Mécanisme |
|---|---|---|
| **Hauteur visible (morph)** | `.modal-clip` | `height` animée par ressort CSS (`--transition-spring-light` — voir note ci-dessous), pilotée par un ref réactif `clipHeight`. Peut overshooter sans danger : ça ne fait que sur-révéler/sur-masquer quelques px d'un scroller déjà posé. |

> **Note tuning (post-rewrite) — ressort du clip = `--transition-spring-light`.** À l'origine le clip portait `--transition-spring-slow` (1.6s, overshoot 1.0315), choisi pour son overshoot quasi-nul **uniquement** parce que l'ancien couplage clampait le scroll sur overshoot. Le découplage scroller/ressort ayant supprimé cette contrainte (l'overshoot ne fait plus que sur-révéler/sur-masquer quelques px de fond, borné par `max-height:100%`), le ressort a été accéléré à `--transition-spring-light` (0.82s, overshoot 1.073) pour un morph plus vif. Le choix est purement esthétique et se règle en changeant cette seule variable sur `.modal-clip`.
| **Hauteur du viewport scrollable** | `.modal-scroller` | `height` explicite px = **cible settled** (`min(contenu+padding, getMaxHeight())`), posée **synchronement, `transition:none`**. `clientHeight` indépendant du ressort. |
| **Scroll** | `.modal-scroller` | `overflow-y:auto` permanent, jamais togglé. Une seule vue montée ⇒ `scrollTop` est une valeur unique sauvée/restaurée manuellement. |
| **Stacking du cross-fade** | `.view-stack` (grid) | réserve `max(sortant,entrant)` ⇒ pas de crop, pas de pin manuel. |
| **Cross-fade du titre** | `NavigationHeader` interne | `header-fade` grid-stack existant (NavigationHeader.vue:115-127). Header fixe ⇒ pas d'offset. |

**Un seul writer de hauteur.** `useAnimatedHeight` est refactoré : une fonction unique `setTargetHeight(px)` écrit **les deux** refs ensemble :
- `clipHeight = px` (lié au style de `.modal-clip`, animé) ;
- `scrollerHeight = px` (lié au style de `.modal-scroller`, `transition:none`).

Le ResizeObserver (sur `.view-stack`/contentInner) appelle `setTargetHeight` en régime permanent. La nav appelle `setTargetHeight` une fois (cible mesurée). Comme les deux refs passent par la **même** fonction, elles ne peuvent jamais diverger ⇒ plus de height lock, plus de double-spring (C5/C6). `requestHeightDelta` reste exposé pour les accordéons (I15) mais devient un **wrapper trivial** : il calcule `target = current + delta` (clampé à max) et appelle `setTargetHeight(target)`. Plus de `isHeightLocked`/`unlockTimer`/`skipUnlockCorrection`/`targetStillAtMax` — l'observer et la nav posant la **même** valeur via le même writer, il n'y a plus rien à verrouiller (l'observer est idempotent contre une valeur qu'on vient d'écrire).

### 3.4 Mécanisme du morph

Le morph visuel = `.modal-clip.height` ressort sur un scroller déjà posé à sa hauteur finale.
- **Grandir** (court→haut) : scroller posé à la hauteur finale (plus grande) ⇒ clip s'ouvre par ressort pour la révéler.
- **Rétrécir** (haut→court) : scroller posé à la hauteur finale (plus petite) ⇒ clip se ferme par ressort ; `overflow:hidden` du clip masque le bas pendant que le ressort rattrape.
- **Les deux overflow (at-max)** : `target_old == target_new == getMaxHeight()` ⇒ `|Δ| < 2` ⇒ pas de transition de clip ; scroller déjà à max ; seul `scrollTop` change (le cas « both overflow » tombe gratuitement, plus de `targetStillAtMax`). C10 : dans ce cas le clip n'anime pas ⇒ pas d'overshoot ⇒ le 24px n'est plus load-bearing.

Pendant tout le ressort, `scroller.clientHeight == cible finale` ⇒ `maxScroll` correct dès la frame 0 ⇒ **scroll vivant et écriture `scrollTop` qui atterrit** (C1, C2).

### 3.5 Séquence frame-par-frame — FORWARD (push)

> `push()` mute la stack puis appelle `prepareNavigation()` (SettingsModal.vue:364-366), AVANT le patch Vue. Vue patche au prochain tick : la vue entrante apparaît dans `.view-stack`.

1. **`prepareNavigation()`** (synchrone, avant patch) : scrub des styles inline résiduels d'une nav interrompue (header/entrant) — **sans clone** (supprimé). Capture `leavingHeight = scroller.scrollTop`-context inutile ici ; on capture la hauteur sortante pour le delta.
2. **`onBeforeLeave(el)`** : la vue sortante est empilée dans la cellule grid (plus de `position:absolute` ; le grid-stack la maintient à sa taille). Forward non-scrollé : reset `scrollTop=0` immédiat. Forward scrollé : la sortante reste à son offset car la cellule grid la tient ; aucun clone.
3. **Patch Vue** : la vue entrante est insérée dans la cellule grid (à scrollTop=0 visuel, c'est le haut).
4. **`onEnter(el)` + 1 rAF** : mesurer `target = clamp(contentInner.offsetHeight + padding, getMaxHeight())`. Appeler `setTargetHeight(target)` :
   a. écrit `scroller.style.height = target` (`transition:none`) ;
   b. **force reflow** (`scroller.offsetHeight`) ⇒ `clientHeight`/`scrollHeight` du scroller flushés AVANT toute écriture scroll ;
   c. écrit `clipHeight = target` ⇒ le clip démarre son ressort.
5. **Cross-fade** : `header-fade` interne anime le titre ; les deux vues fade out-then-in dans la cellule. Scroll vivant.
6. **`onAfterLeave()`** (synchrone, pas de defer) : `scroller.scrollTop = 0` (forward). Comme le scroller est à hauteur finale, l'écriture atterrit. Aucun style à reset (pas de clone, pas de CSS-offset).

### 3.6 Séquence frame-par-frame — BACK (restore T>0)

> `back()` pop la stack et pose `pendingScrollRestore = T` (useNavigationStack.js:47-54), puis `prepareNavigation()` (SettingsModal.vue:367).

1. **`prepareNavigation()`** : scrub résiduels. `pendingScrollRestore = T` est lu plus tard comme discriminant forward/back.
2. **`onBeforeLeave(el)`** : sortante empilée dans la cellule grid à sa taille. Pas de clone (header fixe).
3. **Patch Vue** : vue de destination insérée dans la cellule.
4. **`onEnter(el)` + 1 rAF** : mesurer `target` de la destination. `setTargetHeight(target)` :
   a. `scroller.style.height = target` (`transition:none`) ;
   b. **force reflow** (`scroller.offsetHeight`) — **load-bearing sur WebKit** : sans ce read, WebKit peut ne pas flusher le layout entre l'écriture height et l'écriture scrollTop (point cassé par les juges sur #2/#3) ;
   c. **`scroller.scrollTop = T`** — atterrit immédiatement car `maxScroll = scrollHeight - clientHeight` est déjà final (le clip n'influe pas) ;
   d. `clipHeight = target` ⇒ ressort du clip.
   ⇒ La vue de destination est **déjà à l'offset T dès la frame 0**, sans CSS-offset trick, sans flash-then-jump (C9). Header fixe ⇒ déjà aligné, son `header-fade` cross-fade le titre en place.
5. **Cross-fade** + ressort du clip par-dessus. Scroll vivant à T.
6. **`onAfterLeave()`** (synchrone) : `onScrollRestored?.()` ⇒ consumer vide `pendingScrollRestore` (SettingsModal.vue:348). Aucun reset de style.

> **Ordre load-bearing (back) : height → reflow → scrollTop → clipHeight, dans le MÊME tick d'`onEnter`+rAF.** C'est la seule contrainte d'ordre, et elle est synchrone donc trivialement testable. Corrige le grief WebKit des juges : on **mandate** le `offsetHeight` read entre height et scrollTop.

### 3.7 Ce qu'on SUPPRIME

- `deferScrollRestore` + tout le bloc finalize différé (Modal.vue:130-212).
- `deferGeneration` + `isStale()` + `cancelDeferredFinalize` + le provide associé (Modal.vue:136-153,163,170,196-211 ; injecté SettingsModal.vue:326,352 / EqualizerModal.vue:147,161).
- Le double-rAF gate (Modal.vue:193-211).
- `isHeightTransitioning` + handlers `transitionstart`/`transitionend`/`transitioncancel` **comme mécanisme de correction scroll** (Modal.vue:79-97,12-13). (Si un futur besoin de callback post-morph apparaît, il lira `getComputedStyle` ou un timer `useTimer` — on ne ressuscite pas le flag.)
- `.overflow-transitioning` + sa règle `overflow:hidden` + l'override inline `overflowY` (Modal.vue:15,176,179,185-190,547-551).
- Le **clone de header** : cloneNode, strip `.actions-container`, insert, absolute+translateY, fade, et les 3 sites de scrub inline (useViewTransition.js:84-103,113-124,157-188,348-364).
- Le **CSS-offset trick** (entrant `position:absolute`, `top=savedScrollTop-targetScroll`, useViewTransition.js:219-272) + le workaround `savedInnerHeight`/pollution `scrollHeight` (useViewTransition.js:107,288-298).
- `pinnedWrapper` minHeight pin/expand/release (useViewTransition.js:145-151,234-240,381-385) — remplacé par grid-stack.
- `isHeightLocked` + `unlockTimer` + `skipUnlockCorrection` + `targetStillAtMax` + le chemin prédictif de `requestHeightDelta` (useAnimatedHeight.js:32-35,99-193) — remplacés par le writer unique `setTargetHeight`.
- La règle `:deep(.fade-slide-leave-active){position:absolute}` (SettingsModal.vue:697-702, EqualizerModal.vue dupliqué) — remplacée par grid-stack.
- Le binding `:style="{ height: containerHeight }"` sur `.modal-container` (Modal.vue:11) — déplacé sur `.modal-clip`.

### 3.8 Ce qu'on GARDE (et pourquoi)

- **`requestHeightDelta`** comme API injectée publique (I15) — réécrit en wrapper trivial de `setTargetHeight`. Appelants inchangés : ToggleSection.vue:85, MultiroomControl.vue:352,359, NetworkSettings.vue:316-360.
- **`resetFirstResize`** / `isFirstResize` (useAnimatedHeight.js:67-73,95-97) — pour l'init sans ressort à l'ouverture et la croissance async (scénarios « Ouverture », « Croissance async »). `setTargetHeight` pose alors les deux hauteurs sans transition.
- **ResizeObserver** sur contentInner (useAnimatedHeight.js:46-86) — seul driver en régime permanent (croissance async, rotation viewport). Threshold >2px (jitter) conservé.
- **NavigationHeader** + son `header-fade`/`actions-fade` internes (NavigationHeader.vue:9-44,115-127,198-228) — réutilisés tels quels ; on les **sort** juste du scroller.
- **`useNavigationStack`** (save/back/pendingScrollRestore) — inchangé. La restauration est juste écrite synchronement au lieu d'être différée.
- **24px bounceMargin** (Modal.vue:69) — gardé comme respiration cosmétique (clip overshoot ne touche plus la correction scroll). Réglable après revue visuelle (open Q).
- Open/close, body-lock, auto-close, `useTimer` — inchangés.
- **AudioSourceLayout** — la branche `if (requestHeightDelta)` / `if (deferScrollRestore)` disparaît côté Modal mais reste optionnelle dans `useViewTransition` ; AudioSourceLayout ne passe toujours rien ⇒ chemin allégé. Vérifier que `prepareNavigation` y a encore un rôle (sinon devenu no-op, open Q).

---

## 4. Architectures écartées

- **#1 Per-view scroller + keep-alive (14/20).** Rejetée : viole I6, importe le coût mémoire/CPU keep-alive sur Pi, et **se casse** sur deux points laissés ouverts — (a) les vues param-driven (`zoneGroupId` SettingsModal.vue:385, `macIdToEdit` :388, `stationToEdit`) sont des **refs partagées** ⇒ deux instances de la même vue à des profondeurs différentes partageraient un pane + son scrollTop (back-restore au mauvais offset) ; (b) `content-visibility:hidden` (sa propre mitigation perf Pi) **contredit** sa rétention native de scrollTop (pane inerte = pas de géométrie scroll ⇒ flash-then-jump au back). On en garde seulement le grid-stack.
- **#4 FLIP-Morph (transform-only sur clip layer) (11/20).** Rejetée : iOS 2/5. Animer un `transform` sur un **ancêtre** d'un scroller à momentum (`.modal-clip` ancêtre de `.modal-scroller`) est le déclencheur classique de désync hit-test/scroll-offset sur WebKit ; la variante scaleY **écrase verticalement le contenu live** ~1,6 s, et le counter-scale est un transform imbriqué sans précédent dans ce repo. De plus, **supprime `requestHeightDelta` sans remplacement** ⇒ casse les accordéons ToggleSection/Multiroom/Network (I15). Cassée par : « accordéon intra-vue » et « back en scrollé pendant un fling ».
- **#2/#3 prises seules.** Solides sur les bugs cibles mais **cassées par les juges** sur (a) le **faux postulat « header sticky/hors scroller »** (aucun `position:sticky` dans le code ; le header scrolle, SettingsModal.vue:5) ⇒ désync header sur nav scrollée ; (b) le **forced reflow non mandaté** entre height et scrollTop sur WebKit. La présente archi **les retient en corrigeant ces deux points** : on sort réellement le header du scroller (§3.1.4) et on **mandate** le `offsetHeight` read (§3.6).

---

## 5. Plan de migration

> Contrainte repo : appliance, un seul bundle `dist/`, **aucune infra de feature-flag** (zéro `VITE_`/`FEATURE_` hors `import.meta.env.DEV/MODE`), et CLAUDE.md interdit les flags legacy/compat. Donc **pas de flag runtime en prod**. Revert = `git revert` du commit + rebuild `dist/`. On compense par un ordre de bataille « plus petit levier d'abord » testable en dev, pour qu'un revert isolé soit toujours possible étape par étape.

### Ordre de bataille (chaque étape commit-able et revertible indépendamment)

1. **Étape 0 — Nettoyage debug (zéro risque comportemental).** Retirer tout `[DEBUG-SCROLL]` / `modalDebugLog`/`modalDebugTrace` (Modal.vue:83-128 etc.) qui parasite la lecture. *Testable en dev.*
2. **Étape 1 — Clone du header → `transform` sur vue sortante (header GARDÉ défilant, D1).** Le header reste dans le scroller. Remplacer toute la machinerie de clone (cloneNode/insert/absolute/translateY/cleanup, useViewTransition.js) par : sur nav scrollée, `transform: translateY(-scrollTop)` sur la vue sortante le temps du cross-fade, et s'appuyer sur le `header-fade` interne pour le titre. **Couplé au grid-stack (étape 2) qui empile sortante/entrante.** *Logique testable en dev ; **back-restore scrollé à valider iPhone + Pi**.*
3. **Étape 2 — Grid-stack des vues.** Remplacer `:deep(.fade-slide-leave-active){position:absolute}` par `.view-stack{display:grid}` une cellule + `<Transition>`. Supprimer `pinnedWrapper` (useViewTransition.js:145-151,234-240,381-385). Garder temporairement le reste. *Testable en dev (crop/flash), validation Pi pour le double-paint.*
4. **Étape 3 — Insertion `.modal-clip` + scroller à hauteur explicite.** Ajouter le nœud clip, déplacer la `height` animée dessus, refactorer `useAnimatedHeight` en `setTargetHeight` unique écrivant clip+scroller, réécrire `requestHeightDelta` en wrapper. **Cœur du fix.** Ré-allouer radius/glass-stroke/padding (Modal.vue:507-522,539). *Validation iPhone + Pi obligatoire (forced reflow WebKit).*
5. **Étape 4 — Suppression de la machinerie morte.** Retirer deferScrollRestore + génération + double-rAF + `.overflow-transitioning` + clone + CSS-offset + savedInnerHeight + height lock + skipUnlockCorrection (tous les sites §3.7). Réécrire `useViewTransition` en version synchrone (`onAfterLeave` écrit `scrollTop` direct). *Testable en dev pour la logique, **back-restore + rapid-nav à valider iPhone + Pi**.*
6. **Étape 5 — Vérif consommateurs.** EqualizerModal (`flush:'pre'`), AudioSourceLayout (chemin opt-in intact, `prepareNavigation` éventuellement no-op), accordéons (ToggleSection/Multiroom/Network via `requestHeightDelta`). *Dev + Pi.*

### Testable en dev (Vite) vs exige device
- **Dev (logique, pas de feel)** : structure DOM, suppression des branches, `requestHeightDelta` toujours appelable, EqualizerModal `flush:'pre'`, AudioSourceLayout non régressé, delta<2px skip.
- **Exige iPhone PWA + kiosk Pi** : tout ce qui dépend du **timing du forced reflow WebKit** (back-restore landing), du **momentum/rubber-band**, du jank de ressort sur thread Pi lent, du double-paint grid-stack, et du changement visuel header-fixe. Bridge simulateur dispo (Pi→mac-mini→`xcrun simctl`, cf. mémoire iOS PWA).

---

## 6. Matrice de tests device

> Légende résultat : ✅ = comportement attendu (cf. §2.2). Chaque case = à cocher manuellement sur la cible.

| Scénario | iOS PWA (WebKit) | Kiosk Pi (thread lent) |
|---|---|---|
| Ouverture (init sans ressort, scroll utilisable de suite) | Pas de spring-from-0 ; scroll dès layout | Idem ; pas de jank d'init |
| Fermeture | Scale/fade ~180ms, body-lock libéré | Idem |
| Forward court→haut | Ressort UP fluide, scrollTop=0, scroll vivant | Ressort UP sans frame-drop visible |
| Forward haut→court | Ressort DOWN, pas de crop sortant | Idem ; pas de double-paint visible |
| **Forward EN SCROLLÉ** | Sortante figée à l'offset, pas de jump-to-top, header cross-fade propre | Idem |
| **Back AVEC restore (T>0)** | **Atterrit à T dès frame 0, pas de flash-then-jump** (valide le forced reflow) | **Atterrit à T, pas de gel** |
| Les deux overflow (max) | Pas d'animation hauteur, seul scroll change | Pas de double-spring |
| Delta <2px | Cross-fade instantané, pas de ressort | Idem |
| Push/back/push rapides | Dernière nav gagne, zéro orphelin/écrasement | Idem, pas de correction parasite |
| Interruption mid-spring | Ressort repris, scroll non écrasé, header reset | Idem |
| Nav non-scrollée vers top | Cross-fade + ressort simples | Idem |
| EqualizerModal disabled↔loading↔controls | Cross-fade + (rare) scroll OK, `flush:'pre'` | Idem |
| Croissance async post-nav | Re-ressort, clamp max, scrollTop préservé | Idem |
| **Accordéon intra-vue** (Toggle/Multiroom/Network) | `requestHeightDelta` ⇒ ressort lock-step avec grid interne | Idem |
| Momentum/fling puis nav | Pas de bleed/glitch de scroll (point de vigilance WebKit) | N/A (pas de momentum tactile kiosk) |
| Auto-close 120s à travers navs | Reset OK | Reset OK |
| Body-scroll lock | Verrouillé tant qu'ouvert | Idem |
| AudioSourceLayout (Radio/Podcast) | Gradient + nav inchangés | Inchangés |

---

## 7. Risques ouverts & décisions à prendre (à trancher avec le owner)

1. **UX header — RÉSOLUE → header DÉFILANT (D1).** On garde le header dans le scroller (look actuel inchangé). Le clone est tout de même supprimé, non pas en fixant le header, mais via `transform: translateY(-scrollTop)` sur la **vue sortante** + le `header-fade` interne (cf. D1 et décision #3 ci-dessous, qui devient le mécanisme retenu). Reste à valider sur device le cas **back-restore scrollé**.
2. **Forced reflow WebKit — à valider on-device.** L'ordre height→`offsetHeight`→scrollTop→clipHeight (§3.6) est la clé du back-restore. Le grief des juges sur #2/#3 = WebKit ne flush pas toujours sans ce read. *On le mandate ; à confirmer iPhone que le read suffit (pas de double rAF nécessaire).*
3. **Forward-scrolled : reset scrollTop=0 sur scroller partagé — RÉSOLUE (étape 5).** Mécanisme retenu : **figer la vue sortante par `position:relative; top:-oldScrollTop` ET écrire `scrollTop=0` immédiatement, le tout synchrone dans `onBeforeLeave`** (useViewTransition.js:97-103). L'option « différer le `scrollTop=0` à `onAfterLeave` » est écartée — la figer + écrire de suite atterrit dès la frame 0. `position:relative` (et non `transform`) garde la sortante dans le flux grid (la cellule réserve toujours `max(sortant,entrant)`) et laisse `transform` libre pour le fade-slide. *Reste à eyeball Pi/iPhone le rendu, pas le choix.*
4. **24px bounceMargin (Modal.vue:73) — RÉSOLUE (étape 5) → GARDÉ à 24px.** Purement cosmétique : `max-height:100%` sur `.modal-clip`/`.modal-shell` borne déjà l'overshoot du ressort sous le bord viewport, donc le 24px n'absorbe pas l'overshoot — c'est juste la respiration entre une modale at-max et le bord. 24px = valeur connue-bonne ; le réduire est un micro-réglage visuel sans urgence, laissé à l'eyeball owner.
5. **ResizeObserver écrit `scrollerHeight` à chaque changement — RÉSOLUE.** Pas de boucle de feedback : l'observer est sur `contentInner` (hauteur dictée par le contenu), pas sur le scroller ; écrire `scroller.style.height` ne change pas le `contentRect` observé. La nav écrit la même cible idempotemment, le threshold >2px absorbe le jitter. Observer laissé actif en permanence.
6. **AudioSourceLayout : `prepareNavigation` — RÉSOLUE (étape 5) → PAS no-op, GARDÉ.** AudioSourceLayout a un vrai scroller (`overflow-y:auto`) et utilise donc le gel de la vue sortante (`onBeforeLeave`/`onEnter` posent `position:relative; top`). `prepareNavigation` scrube ce reliquat inline sur une nav interrompue — rôle réel, conservé (AudioSourceLayout.vue:207-212).
7. **`modalDebug` (modalDebugLog/Trace) — RÉSOLUE.** `services/modalDebug.js` supprimé et retiré de l'allowlist console — vérifié absent (zéro référence repo-wide).
8. **Scale de fermeture × scroller à hauteur explicite — RÉSOLUE (étape 5).** Le `scale(0.98)` de close porte sur `.modal-shell`, qui a `overflow:hidden` + `border-radius` : il clippe tout le sous-arbre mis à l'échelle (clip + scroller), donc la hauteur explicite du scroller ne peut pas révéler d'overflow aux coins arrondis. *Confirmation visuelle device à cocher (§6).*
