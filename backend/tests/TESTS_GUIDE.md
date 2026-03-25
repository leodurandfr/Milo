# 🧪 Guide des Tests Milo

## ✅ Tous les tests passent : 40/40

```
======================== 40 passed in 17.47s ==============================
```

---

## 🚀 Comment lancer les tests

### Commande de base (depuis ~/milo)
```bash
source venv/bin/activate && python -m pytest backend/tests/
```

### Avec détails (mode verbeux)
```bash
source venv/bin/activate && python -m pytest backend/tests/ -v
```

### Tests rapides (sans détails)
```bash
source venv/bin/activate && python -m pytest backend/tests/ -q
```

---

## 📊 Options utiles

### Lancer un seul fichier de tests
```bash
source venv/bin/activate && python -m pytest backend/tests/test_state_machine.py
```

### Lancer un seul test spécifique
```bash
source venv/bin/activate && python -m pytest backend/tests/test_state_machine.py::TestUnifiedAudioStateMachine::test_initialization
```

### Voir la couverture de code
```bash
source venv/bin/activate && python -m pytest backend/tests/ --cov=backend --cov-report=term-missing
```

### Générer un rapport HTML de couverture
```bash
source venv/bin/activate && python -m pytest backend/tests/ --cov=backend --cov-report=html
# Puis ouvrir : htmlcov/index.html
```

---

## 📂 Structure des tests

```
backend/tests/
├── conftest.py                  # Fixtures partagées (mocks)
├── test_state_machine.py        # 20 tests pour la machine à états
└── test_routing_service.py      # 20 tests pour le routage audio
```

---

## 🎯 Quand lancer les tests ?

✅ **Avant de committer** du code
✅ **Après avoir modifié** `state_machine.py` ou `routing_service.py`
✅ **Avant de déployer** une nouvelle version
✅ **Régulièrement** pour vérifier l'intégrité du système

---

## 🔍 Que testent ces tests ?

### Tests de `state_machine` (20 tests)
- ✅ Transitions entre sources audio (Spotify, Bluetooth, ROC)
- ✅ Gestion des timeouts
- ✅ Protection contre les transitions concurrentes
- ✅ Broadcast d'événements WebSocket
- ✅ Gestion de l'état multiroom/equalizer

### Tests de `routing_service` (20 tests)
- ✅ Activation/désactivation du multiroom
- ✅ Activation/désactivation de l'equalizer
- ✅ Rollback automatique en cas d'échec
- ✅ Sauvegarde persistante des settings
- ✅ Retry automatique en cas d'erreur systemd

---

## 🐛 Interprétation des résultats

### ✅ Tous les tests passent
```
======================== 40 passed in 17.47s ==============================
```
→ Tout fonctionne correctement !

### ❌ Un test échoue
```
======================== 1 failed, 39 passed ==============================
FAILED test_state_machine.py::test_transition_timeout
```
→ Un problème a été détecté. Vérifier le code modifié.

### ⚠️ Un test est skippé
```
======================== 39 passed, 1 skipped =========================
```
→ Un test a été désactivé temporairement (normal dans certains cas)

---

## 💡 Conseils

### Workflow de développement recommandé

1. **Modifier le code**
   ```bash
   nano backend/infrastructure/state/state_machine.py
   ```

2. **Lancer les tests**
   ```bash
   source venv/bin/activate && python -m pytest backend/tests/ -v
   ```

3. **Si un test échoue :**
   - Lire le message d'erreur
   - Corriger le code
   - Relancer les tests
   - Répéter jusqu'à ce que tous les tests passent

4. **Committer les changements**
   ```bash
   git add .
   git commit -m "Fix: correction du bug X"
   ```

---

## 📚 Documentation

Pour plus de détails sur les tests, voir :
- `backend/tests/README.md` - Documentation complète des tests
- `QUICK_WINS_SUMMARY.md` - Résumé des améliorations apportées

---

## 🎓 Pour aller plus loin

### Ajouter un nouveau test
```python
# Dans backend/tests/test_state_machine.py

@pytest.mark.asyncio
async def test_mon_nouveau_test(self, state_machine):
    """Test de ma nouvelle fonctionnalité"""
    # Arrange (préparation)
    state_machine.system_state.active_source = AudioSource.SPOTIFY

    # Act (action)
    result = await state_machine.ma_fonction()

    # Assert (vérification)
    assert result is True
    assert state_machine.system_state.source_state == SourceState.CONNECTED
```

### Débugger un test qui échoue
```bash
# Lancer le test avec plus de détails
source venv/bin/activate && python -m pytest backend/tests/test_state_machine.py::test_mon_test -vv -s

# -vv : très verbeux
# -s  : affiche les print() dans le code
```

### Mesurer le temps d'exécution des tests
```bash
source venv/bin/activate && python -m pytest backend/tests/ --durations=10

# Affiche les 10 tests les plus lents
```

---

## ✨ Résumé

**40 tests** valident automatiquement le comportement des composants critiques de Milo.
**17 secondes** pour valider que tout fonctionne.
**0 modification** nécessaire dans votre code existant.

**Commande à retenir** :
```bash
source venv/bin/activate && python -m pytest backend/tests/ -v
```

🎉 Tous vos tests passent ! Votre application est robuste.
