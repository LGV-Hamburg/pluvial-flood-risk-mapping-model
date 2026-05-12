Test modules live in this folder.

Run only unit/regression tests (default local fast path):

```bash
python -m pytest -m "not e2e"
```

Run integration tests that require Redis:

```bash
python -m pytest -m integration
```

Run end-to-end tests that execute the API directly from Python:

```bash
RUN_E2E=1 python -m pytest -m e2e tests/test_e2e_pipeline.py
```

Expected runtime dependencies for e2e:
- Redis container running
- API server running on `http://localhost:8000`
- Celery worker running