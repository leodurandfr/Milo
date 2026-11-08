import UIKit
import WebKit

class ViewController: UIViewController, WKNavigationDelegate, WKUIDelegate {

    var webView: WKWebView!
    var errorView: UIView!
    var reconnectingOverlay: UIView!
    var logoImageView: UIImageView!
    var titleLabel: UILabel!
    var messageLabel: UILabel!

    var connectivityTimer: Timer?
    var initialErrorTimer: Timer?
    var isConnected = false {
        didSet {
            guard isConnected != oldValue else { return }
            scheduleConnectivityCheck()
            setNeedsStatusBarAppearanceUpdate()
        }
    }

    /// Le contenu qui passe sous la status bar est-il sombre ? Mesuré sur le
    /// rendu de la page, pas déduit de son thème : une vue Paroles, une tuile
    /// sombre ou le thème sombre comptent tous.
    private var contentUnderStatusBarIsDark = false {
        didSet {
            guard contentUnderStatusBarIsDark != oldValue else { return }
            setNeedsStatusBarAppearanceUpdate()
        }
    }

    override var preferredStatusBarStyle: UIStatusBarStyle {
        // Hors page (erreur, logo de reconnexion), le fond est clair.
        isConnected && contentUnderStatusBarIsDark ? .lightContent : .darkContent
    }

    /// Vrai dès qu'une première navigation a abouti ou échoué. Sert à reconnaître
    /// le lancement à froid, pendant lequel UIKit émet `sceneDidEnterBackground`
    /// puis `sceneWillEnterForeground` alors que `viewDidLoad` vient déjà de
    /// lancer le chargement.
    private(set) var hasCompletedFirstLoad = false

    /// Date du passage en arrière-plan, pour juger si la page mérite un rechargement
    private var backgroundedAt: Date?

    /// Cadence du sondage de `milo.local` : espacée quand tout va bien, serrée
    /// quand on attend le retour de Milō.
    private let connectedPollInterval: TimeInterval = 15
    private let disconnectedPollInterval: TimeInterval = 3

    /// En deçà de ce temps passé en arrière-plan, la page est réputée encore valide :
    /// la recharger perdrait l'état de la SPA (scroll, vue courante) et
    /// retéléchargerait une vingtaine de sous-ressources pour rien.
    private let reloadAfterBackgroundInterval: TimeInterval = 30

    override func loadView() {
        _ = UIScrollView.hideTopEdgeEffectInWebViews
        let containerView = UIView()
        containerView.backgroundColor = UIColor(red: 0.97, green: 0.97, blue: 0.97, alpha: 1.0)
        self.view = containerView

        // WebView - CHANGEMENTS ICI pour éviter le flickering
        // Un `window.open` qui suit un `await` n'a plus de geste de l'utilisateur
        // derrière lui, et iOS le bloque par défaut avant même d'appeler le
        // délégué — c'est le cas de « Se connecter » dans Réglages → Qobuz, qui
        // demande l'URL à Milō avant d'ouvrir. La contrepartie, une page qui
        // ouvre sans qu'on ait touché, est bornée par `createWebViewWith` : rien
        // d'autre que du web, et toujours dans le navigateur.
        let configuration = WKWebViewConfiguration()
        configuration.preferences.javaScriptCanOpenWindowsAutomatically = true
        configuration.userContentController.add(WeakScriptMessageHandler(self), name: Self.statusBarMessage)
        for source in [Self.statusBarBlurScript, Self.statusBarWatcherScript] {
            configuration.userContentController.addUserScript(WKUserScript(
                source: source, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        }
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.backgroundColor = UIColor(red: 0.97, green: 0.97, blue: 0.97, alpha: 1.0) // ← Changé
        webView.isOpaque = true // ← Changé
        webView.scrollView.backgroundColor = UIColor(red: 0.97, green: 0.97, blue: 0.97, alpha: 1.0) // ← Changé
        webView.alpha = 0.0
        containerView.addSubview(webView)

        setupErrorView()
        setupReconnectingOverlay()
    }
    
    func setupErrorView() {
        // Vue d'erreur - VISIBLE au démarrage (on la masquera si connexion OK)
        errorView = UIView()
        errorView.backgroundColor = UIColor(red: 0.97, green: 0.97, blue: 0.97, alpha: 1.0)
        errorView.alpha = 0.0 // Invisible mais pas hidden
        errorView.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(errorView)
        
        let stackView = UIStackView()
        stackView.axis = .vertical
        stackView.alignment = .center
        stackView.spacing = 0
        stackView.translatesAutoresizingMaskIntoConstraints = false
        errorView.addSubview(stackView)
        
        // Logo
        logoImageView = UIImageView(image: UIImage(named: "Logo"))
        logoImageView.contentMode = .scaleAspectFit
        logoImageView.translatesAutoresizingMaskIntoConstraints = false
        stackView.addArrangedSubview(logoImageView)
        
        // Spacer 64px
        let spacer64 = UIView()
        stackView.addArrangedSubview(spacer64)
        
        // Titre
        titleLabel = UILabel()
        titleLabel.text = String(localized: "errorUnavailableTitle")
        titleLabel.textAlignment = .center
        titleLabel.font = UIFont(name: "NeueMontreal-Medium", size: 18) ?? .systemFont(ofSize: 18, weight: .medium)
        titleLabel.textColor = UIColor(red: 0x76/255.0, green: 0x7C/255.0, blue: 0x76/255.0, alpha: 1.0)
        stackView.addArrangedSubview(titleLabel)
        
        // Spacer 8px
        let spacer8 = UIView()
        stackView.addArrangedSubview(spacer8)
        
        // Message
        messageLabel = UILabel()
        messageLabel.text = String(localized: "errorUnavailableMessage")
        messageLabel.textAlignment = .center
        messageLabel.numberOfLines = 0
        messageLabel.font = UIFont(name: "NeueMontreal-Medium", size: 16) ?? .systemFont(ofSize: 16, weight: .medium)
        messageLabel.textColor = UIColor(red: 0xA6/255.0, green: 0xAC/255.0, blue: 0xA6/255.0, alpha: 1.0)
        stackView.addArrangedSubview(messageLabel)
        
        NSLayoutConstraint.activate([
            errorView.topAnchor.constraint(equalTo: view.topAnchor),
            errorView.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            errorView.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            errorView.bottomAnchor.constraint(equalTo: view.bottomAnchor),
            
            stackView.centerXAnchor.constraint(equalTo: errorView.centerXAnchor),
            stackView.centerYAnchor.constraint(equalTo: errorView.centerYAnchor),
            stackView.leadingAnchor.constraint(greaterThanOrEqualTo: errorView.leadingAnchor, constant: 40),
            stackView.trailingAnchor.constraint(lessThanOrEqualTo: errorView.trailingAnchor, constant: -40),
            
            logoImageView.widthAnchor.constraint(equalToConstant: 86),
            logoImageView.heightAnchor.constraint(equalToConstant: 48),
            spacer64.heightAnchor.constraint(equalToConstant: 64),
            spacer8.heightAnchor.constraint(equalToConstant: 8)
        ])
    }

    func setupReconnectingOverlay() {
        reconnectingOverlay = UIView()
        reconnectingOverlay.backgroundColor = UIColor(red: 0.97, green: 0.97, blue: 0.97, alpha: 1.0)
        reconnectingOverlay.alpha = 0.0
        reconnectingOverlay.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(reconnectingOverlay)

        let logo = UIImageView(image: UIImage(named: "Logo"))
        logo.contentMode = .scaleAspectFit
        logo.translatesAutoresizingMaskIntoConstraints = false
        reconnectingOverlay.addSubview(logo)

        NSLayoutConstraint.activate([
            reconnectingOverlay.topAnchor.constraint(equalTo: view.topAnchor),
            reconnectingOverlay.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            reconnectingOverlay.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            reconnectingOverlay.bottomAnchor.constraint(equalTo: view.bottomAnchor),

            logo.centerXAnchor.constraint(equalTo: reconnectingOverlay.centerXAnchor),
            logo.centerYAnchor.constraint(equalTo: reconnectingOverlay.centerYAnchor),
            logo.widthAnchor.constraint(equalToConstant: 86),
            logo.heightAnchor.constraint(equalToConstant: 48)
        ])
    }

    func showReconnectingOverlay() {
        reconnectingOverlay.alpha = 1.0
    }

    func hideReconnectingOverlay() {
        UIView.animate(withDuration: 0.3) {
            self.reconnectingOverlay.alpha = 0.0
        }
    }

    func handleReturnFromBackground() {
        // Lancement à froid : UIKit émet `sceneDidEnterBackground` puis
        // `sceneWillEnterForeground` dans la foulée de `viewDidLoad`. Recharger ici
        // lancerait une seconde navigation qui annulerait la première.
        guard hasCompletedFirstLoad else { return }

        let awayFor = backgroundedAt.map { Date().timeIntervalSince($0) } ?? .infinity
        backgroundedAt = nil

        // Absence courte et page toujours affichée : la recharger ferait repartir
        // la SPA de zéro sans rien apprendre de neuf.
        if isConnected && awayFor < reloadAfterBackgroundInterval {
            hideReconnectingOverlay()
        } else {
            tryConnectToMilo()
        }

        // Sonder tout de suite plutôt qu'au prochain tour du minuteur : le
        // réseau a pu changer pendant que le téléphone était verrouillé, et
        // c'est la sonde qui retire l'overlay quand Milō ne répond pas.
        checkMiloAndConnect()
    }

    func enterBackground() {
        backgroundedAt = Date()
        showReconnectingOverlay()
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        webView.scrollView.contentInsetAdjustmentBehavior = .never
        // La page dessine elle-même sous la status bar : le fondu d’iOS 26 y
        // prend la couleur de fond de la page (claire) et voile les vues sombres.
        webView.scrollView.topEdgeEffect.isHidden = true
        webView.customUserAgent = "Milo-iOS-App/1.0"
        
        // Essayer de se connecter immédiatement
        tryConnectToMilo()
        
        // Afficher le message d'erreur après 2.5 secondes si pas connecté
        initialErrorTimer = Timer.scheduledTimer(withTimeInterval: 2.5, repeats: false) { [weak self] _ in
            if self?.isConnected != true {
                // Afficher la vue d'erreur avec animation
                UIView.animate(withDuration: 0.3) {
                    self?.errorView.alpha = 1.0
                }
            }
        }
        
        // Démarrer le monitoring
        scheduleConnectivityCheck()
    }
    
    func tryConnectToMilo() {
        let url = URL(string: "http://milo.local")!
        navigationStartedAt = Date()
        webView.load(URLRequest(url: url))
    }

    /// Début de la navigation en cours, pour ne pas couper un chargement
    /// seulement lent — voir `checkMiloAndConnect`.
    private var navigationStartedAt = Date.distantPast
    private let staleNavigationInterval: TimeInterval = 8
    
    /// (Re)programme le sondage de `milo.local` à la cadence de l'état courant.
    func scheduleConnectivityCheck() {
        let interval = isConnected ? connectedPollInterval : disconnectedPollInterval
        guard connectivityTimer?.timeInterval != interval else { return }

        connectivityTimer?.invalidate()
        let timer = Timer(timeInterval: interval, repeats: true) { [weak self] _ in
            self?.checkMiloAndConnect()
        }
        // `.common` : en mode `.default`, le sondage s'interrompt tant que
        // l'utilisateur scrolle la webview.
        RunLoop.main.add(timer, forMode: .common)
        connectivityTimer = timer
    }
    
    /// Ce qu'un sondage apprend de Milō.
    enum MiloProbe {
        /// Rien ne répond : Milō est éteint, ou on n'est plus sur son réseau.
        case unreachable
        /// nginx répond, le backend pas encore : Milō démarre, ou redémarre.
        case starting
        /// Le backend répond : la page peut se charger et sortir de son logo.
        case ready
    }

    /// Sonde le backend, pas la page.
    ///
    /// nginx sert la page dès qu'il démarre, bien avant le backend. Sonder `/`
    /// faisait donc charger la page pendant le démarrage de Milō : elle restait
    /// sur son écran au logo, qui n'attend que l'`initial_state` du WebSocket,
    /// et dont les reconnexions s'espacent jusqu'à trente secondes — l'app
    /// semblait bloquée sur le logo de Milō (constaté le 03/10/2026).
    func probeMilo(completion: @escaping (MiloProbe) -> Void) {
        guard let url = URL(string: "http://milo.local/api/audio/state") else { return }

        var request = URLRequest(url: url)
        request.timeoutInterval = 2.0
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData

        URLSession.shared.dataTask(with: request) { _, response, error in
            let status = (response as? HTTPURLResponse)?.statusCode
            // Seuls les codes de passerelle disent « backend absent » : nginx
            // répond, rien derrière. Tout autre code vient du backend lui-même,
            // qui est donc là — une route en erreur ne doit pas cacher toute
            // l'interface derrière « même réseau local ».
            let probe: MiloProbe
            switch status {
            case nil: probe = .unreachable
            case 502, 503, 504: probe = .starting
            default: probe = error == nil ? .ready : .unreachable
            }
            DispatchQueue.main.async { completion(probe) }
        }.resume()
    }

    func checkMiloAndConnect() {
        probeMilo { [weak self] probe in
            guard let self = self else { return }

            // `Task` : la résolution teste maintenant chaque candidat sur le
            // réseau avant de le retenir, ce qui la rend asynchrone. Détachée du
            // sondage à dessein — l'affichage ci-dessous ne l'attend pas, et
            // c'est déjà ce que faisait l'appel bloquant qu'elle remplace.
            if probe == .ready { Task { await MiloAPIClient.resolveAndCacheIPAddress() } }

            if probe == .ready && !self.isConnected {
                // Milō répond sans que sa page soit affichée → (re)charger, sauf si
                // une navigation est déjà en vol : la relancer l'annulerait et
                // ferait repartir le chargement à chaque sondage.
                if !self.webView.isLoading { self.tryConnectToMilo() }
            } else if probe == .unreachable || (probe == .starting && !self.isConnected) {
                // Un backend qui redémarre sous une page affichée (`.starting`,
                // connecté) ne compte pas : la page gère elle-même sa reconnexion.
                self.isConnected = false
                self.showMiloUnavailable()
            }
        }
    }

    /// Milō absent : l'erreur, sans attendre la navigation en vol.
    ///
    /// Sans Wi-Fi, elle peut traîner une minute sur la résolution de
    /// `milo.local`, et seule sa fin retirait l'overlay au logo posé au
    /// verrouillage — on restait sur le logo, sans message (constaté le
    /// 03/10/2026).
    private func showMiloUnavailable() {
        // Arrêtée passé un délai seulement : un chargement juste lent aboutirait,
        // et `didFinish` montrera la page. Une navigation qui traîne, elle,
        // bloque le rechargement — `checkMiloAndConnect` attend `!isLoading`.
        if webView.isLoading, Date().timeIntervalSince(navigationStartedAt) > staleNavigationInterval {
            webView.stopLoading()
        }
        // Un verdict est tombé : sans ça, un premier chargement arrêté ici
        // laisserait `handleReturnFromBackground` sortir à chaque retour,
        // overlay compris.
        hasCompletedFirstLoad = true
        initialErrorTimer?.invalidate()
        hideReconnectingOverlay()
        showErrorView()
    }
    
    func showErrorView() {
        // Afficher la vue d'erreur avec animation
        UIView.animate(withDuration: 0.3) {
            self.errorView.alpha = 1.0
            self.webView.alpha = 0.0
        }
    }
    
    func hideErrorView() {
        // Masquer la vue d'erreur et afficher la webview
        UIView.animate(withDuration: 0.3) {
            self.errorView.alpha = 0.0
            self.webView.alpha = 1.0
        }
    }

    // MARK: - WKNavigationDelegate
    
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        hasCompletedFirstLoad = true

        // Une page chargée ne prouve pas que Milō soit prêt : nginx la sert
        // avant que le backend ne réponde (voir `probeMilo`). La montrer alors,
        // c'est rester sur son logo. On la garde cachée, et le sondage la
        // recharge dès que le backend répond — page et WebSocket d'un coup.
        probeMilo { [weak self] probe in
            guard let self = self else { return }
            guard probe == .ready else {
                self.isConnected = false
                self.hideReconnectingOverlay()
                self.showErrorView()
                return
            }
            self.revealMilo()
        }
    }

    private func revealMilo() {
        isConnected = true

        // Annuler le timer d'erreur initial (connexion réussie)
        initialErrorTimer?.invalidate()

        // Ajouter classe CSS
        webView.evaluateJavaScript("document.body.classList.add('ios-app');", completionHandler: nil)
        sampleContentUnderStatusBar()

        // Synchroniser les settings volume (step + limites) depuis Milo
        Task { await MiloAPIClient.syncVolumeSettings() }

        // Masquer l'erreur et afficher la webview
        hideErrorView()
        hideReconnectingOverlay()
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        // Navigation remplacée par une autre : ce n'est pas un échec de Milō, et la
        // traiter comme tel ferait clignoter l'écran d'erreur.
        if (error as NSError).code == NSURLErrorCancelled { return }

        hasCompletedFirstLoad = true

        // Sans cette remise à zéro, `checkMiloAndConnect` ne retentait jamais après
        // un échec survenu alors qu'on était connecté : sa condition de reconnexion
        // exige `!isConnected`, et l'app restait sur l'écran d'erreur bien que Milō
        // réponde.
        isConnected = false

        // Connexion échouée - masquer l'overlay de reconnexion et afficher l'erreur
        hideReconnectingOverlay()
        showErrorView()
    }

    /// L'échec qui survient *après* que la réponse a commencé d'arriver (page
    /// coupée en cours de chargement) passe ici, et non par
    /// `didFailProvisionalNavigation`. Sans ce délégué, ni `didFinish` ni aucun
    /// chemin d'échec ne tournait : `isConnected` restait vrai et l'overlay de
    /// reconnexion, posé par `enterBackground`, restait affiché au retour.
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        // Même traitement que l'échec provisoire, annulation comprise.
        if (error as NSError).code == NSURLErrorCancelled { return }

        hasCompletedFirstLoad = true
        isConnected = false

        hideReconnectingOverlay()
        showErrorView()
    }

    // MARK: - WKUIDelegate

    /// `window.open` venu de la page (ex. la connexion au compte Qobuz) : sans ce
    /// délégué, WKWebView l'ignore en silence. La page s'ouvre dans le navigateur
    /// par défaut plutôt que dans la webview, qui perdrait l'interface de Milō.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        // Du web seulement : sans geste requis, un `tel:`, un `sms:` ou le
        // schéma d'une autre app partirait sans qu'on ait touché quoi que ce
        // soit. `about:blank` n'aurait de toute façon rien à ouvrir.
        if let url = navigationAction.request.url, ["http", "https"].contains(url.scheme?.lowercased()) {
            UIApplication.shared.open(url)
        }
        return nil
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        webView.frame = view.bounds
        if view.safeAreaInsets.top != sampledStatusBarHeight {
            sampledStatusBarHeight = view.safeAreaInsets.top
            sampleContentUnderStatusBar()
        }
    }

    // MARK: - Contenu sous la status bar

    private var sampledStatusBarHeight: CGFloat = 0

    /// Flou progressif sous la status bar, à la manière de l'App Store, dessiné
    /// par la page elle-même. Le fondu d'iOS 26 ne s'y prête pas : WebKit ne
    /// le déclenche que sur certaines vues, et sa couleur ne suit pas le
    /// contenu (masqué, voir `hideTopEdgeEffectInWebViews`). Un flou UIKit non
    /// plus : il ajoute toujours une teinte, visible au repos sur un fond uni
    /// et sur les modales. `backdrop-filter`, lui, n'en ajoute aucune — un fond
    /// uni flouté reste identique, et l'effet n'apparaît que sous du contenu.
    ///
    /// Trois couches de rayon croissant, chacune masquée plus près du haut :
    /// le flou s'épaissit vers le haut de l'écran et s'éteint au bas de la
    /// status bar, sans rien toucher en dessous — barre de volume, bandeaux.
    /// Au-dessus de tout (la modale est à 5000), sauf le fantôme du
    /// changement de thème, et transparent aux touchers.
    private static let statusBarBlurScript = """
    (() => {
      const style = document.createElement('style');
      style.textContent = `
        #ios-status-bar-blur {
          position: fixed; top: 0; left: 0; right: 0;
          height: env(safe-area-inset-top, 0px);
          pointer-events: none; z-index: 2147483646;
        }
        #ios-status-bar-blur > div { position: absolute; inset: 0; }
        #ios-status-bar-blur > div:nth-child(1) {
          -webkit-backdrop-filter: blur(2px); backdrop-filter: blur(2px);
          -webkit-mask-image: linear-gradient(to bottom, #000 40%, transparent 100%);
          mask-image: linear-gradient(to bottom, #000 40%, transparent 100%);
        }
        #ios-status-bar-blur > div:nth-child(2) {
          -webkit-backdrop-filter: blur(6px); backdrop-filter: blur(6px);
          -webkit-mask-image: linear-gradient(to bottom, #000 20%, transparent 70%);
          mask-image: linear-gradient(to bottom, #000 20%, transparent 70%);
        }
        #ios-status-bar-blur > div:nth-child(3) {
          -webkit-backdrop-filter: blur(12px); backdrop-filter: blur(12px);
          -webkit-mask-image: linear-gradient(to bottom, #000 0%, transparent 45%);
          mask-image: linear-gradient(to bottom, #000 0%, transparent 45%);
        }`;
      document.head.appendChild(style);
      const blur = document.createElement('div');
      blur.id = 'ios-status-bar-blur';
      blur.setAttribute('aria-hidden', 'true');
      for (let i = 0; i < 3; i++) blur.appendChild(document.createElement('div'));
      document.body.appendChild(blur);
    })();
    """

    private static let statusBarMessage = "contentUnderStatusBar"

    /// Prévient l'app quand ce qui passe sous la status bar a pu changer :
    /// défilement de n'importe quel élément (capture, `scroll` ne remonte pas),
    /// vue qui s'ouvre, fin de transition. Au plus un message toutes les
    /// 150 ms. Les changements de `style` ni de texte ne comptent pas : la
    /// progression de lecture en écrit plusieurs par seconde.
    private static let statusBarWatcherScript = """
    (() => {
      let pending = false;
      const ping = () => {
        if (pending) return;
        pending = true;
        setTimeout(() => {
          pending = false;
          window.webkit.messageHandlers.\(statusBarMessage).postMessage(0);
        }, 150);
      };
      document.addEventListener('scroll', ping, { capture: true, passive: true });
      document.addEventListener('transitionend', ping, true);
      document.addEventListener('animationend', ping, true);
      // Un texte remplacé (le temps écoulé, chaque seconde) n'ouvre aucune
      // vue : seuls comptent les éléments ajoutés ou retirés.
      const isElement = (node) => node.nodeType === Node.ELEMENT_NODE;
      new MutationObserver((records) => {
        if (records.some((r) => r.type === 'attributes'
            || [...r.addedNodes].some(isElement) || [...r.removedNodes].some(isElement))) ping();
      }).observe(document.documentElement,
        { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
    })();
    """

    /// Mesure la luminance du rendu de la page sous la status bar, sur une
    /// vignette de quelques pixels. L'écart entre les deux seuils évite que
    /// la status bar clignote sur un contenu de luminance moyenne.
    fileprivate func sampleContentUnderStatusBar() {
        let height = view.safeAreaInsets.top
        guard isConnected, height > 0 else { return }
        let configuration = WKSnapshotConfiguration()
        configuration.rect = CGRect(x: 0, y: 0, width: webView.bounds.width, height: height)
        configuration.snapshotWidth = 16
        webView.takeSnapshot(with: configuration) { [weak self] image, _ in
            guard let self, let luminance = image.flatMap(Self.averageLuminance) else { return }
            if self.contentUnderStatusBarIsDark {
                if luminance > 0.55 { self.contentUnderStatusBarIsDark = false }
            } else if luminance < 0.4 {
                self.contentUnderStatusBarIsDark = true
            }
        }
    }

    private static func averageLuminance(of image: UIImage) -> CGFloat? {
        guard let cgImage = image.cgImage else { return nil }
        let width = 8, height = 2
        var pixels = [UInt8](repeating: 0, count: width * height * 4)
        let drawn = pixels.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: width, height: height,
                                          bitsPerComponent: 8, bytesPerRow: width * 4,
                                          space: CGColorSpaceCreateDeviceRGB(),
                                          bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return false }
            context.interpolationQuality = .medium
            context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return nil }
        var sum: CGFloat = 0
        for i in stride(from: 0, to: pixels.count, by: 4) {
            sum += 0.2126 * CGFloat(pixels[i]) + 0.7152 * CGFloat(pixels[i + 1]) + 0.0722 * CGFloat(pixels[i + 2])
        }
        return sum / CGFloat(width * height) / 255
    }
    
    deinit {
        connectivityTimer?.invalidate()
        initialErrorTimer?.invalidate()
    }
}

/// `WKUserContentController` retient son gestionnaire : passer le contrôleur
/// lui-même le garderait en vie avec sa webview.
private final class WeakScriptMessageHandler: NSObject, WKScriptMessageHandler {
    private weak var target: ViewController?

    init(_ target: ViewController) { self.target = target }

    func userContentController(_ userContentController: WKUserContentController,
                               didReceive message: WKScriptMessage) {
        target?.sampleContentUnderStatusBar()
    }
}

extension UIScrollView {
    /// WebKit donne à chaque élément `overflow: auto` de la page sa propre
    /// UIScrollView, distincte de `webView.scrollView` et créée au fil des
    /// navigations de la SPA. Celle-ci garde le fondu d’iOS 26 sous la status
    /// bar : sur l’écran Radio, c’est `.audio-source-layout` qui défile (le
    /// `html` est en `overflow: hidden`), et ses tuiles s’y floutaient au lieu
    /// de passer dessous. Aucune API publique ne les atteint ; on les prend au
    /// moment où elles entrent dans la fenêtre.
    static let hideTopEdgeEffectInWebViews: Void = {
        let original = #selector(UIView.didMoveToWindow)
        let replacement = #selector(UIScrollView.milo_didMoveToWindow)
        guard let originalMethod = class_getInstanceMethod(UIScrollView.self, original),
              let replacementMethod = class_getInstanceMethod(UIScrollView.self, replacement) else { return }
        // UIScrollView peut hériter `didMoveToWindow` de UIView : l’ajouter
        // d’abord à UIScrollView évite d’échanger celle de toutes les vues.
        if class_addMethod(UIScrollView.self, original,
                           method_getImplementation(replacementMethod), method_getTypeEncoding(replacementMethod)) {
            class_replaceMethod(UIScrollView.self, replacement,
                                method_getImplementation(originalMethod), method_getTypeEncoding(originalMethod))
        } else {
            method_exchangeImplementations(originalMethod, replacementMethod)
        }
    }()

    @objc private func milo_didMoveToWindow() {
        milo_didMoveToWindow()
        guard window != nil else { return }
        var ancestor = superview
        while let view = ancestor {
            if view is WKWebView {
                topEdgeEffect.isHidden = true
                return
            }
            ancestor = view.superview
        }
    }
}
