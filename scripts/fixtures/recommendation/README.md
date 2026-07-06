# recommendation service (demo fixture)

Stand-in Python recommendation service used by the AIOps agent's **s4 capstone**
(code-fix → PR path).

## Build / test
```bash
pip install -r requirements.txt
python -m py_compile $(git ls-files '*.py')
pytest -q
```

`test_memory_is_bounded` fails on the planted-leak revision and passes once the
unbounded module-level accumulation is bounded.
