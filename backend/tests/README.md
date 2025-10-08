# Tests Milo Backend

## Structure

```
tests/
├── __init__.py
├── conftest.py              # Fixtures partagées
├── test_state_machine.py    # Tests UnifiedAudioStateMachine
└── test_routing_service.py  # Tests AudioRoutingService
```

## Exécution

### Tous les tests
```bash
cd backend
pytest
```

### Tests spécifiques
```bash
# Un fichier
pytest tests/test_state_machine.py

# Une classe
pytest tests/test_state_machine.py::TestUnifiedAudioStateMachine

# Une fonction
pytest tests/test_state_machine.py::TestUnifiedAudioStateMachine::test_initialization
```

### Avec couverture
```bash
pytest --cov=backend --cov-report=html
```

### Tests asynchrones uniquement
```bash
pytest -m asyncio
```

## Fixtures disponibles

- `mock_websocket_handler` : Mock du WebSocket handler
- `mock_routing_service` : Mock du routing service
- `mock_plugin` : Mock d'un plugin audio
- `mock_settings_service` : Mock du SettingsService

## Ajout de nouveaux tests

1. Créer un fichier `test_<nom>.py`
2. Utiliser les fixtures de `conftest.py`
3. Marquer les tests async avec `@pytest.mark.asyncio`
4. Utiliser les mocks pour isoler les dépendances

## CI/CD

Ces tests peuvent être intégrés à une pipeline CI/CD :

```yaml
# .github/workflows/tests.yml
- name: Run tests
  run: |
    cd backend
    pytest --cov --cov-report=xml
```
