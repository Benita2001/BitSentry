PY = /opt/miniconda3/bin/python3.13

.PHONY: install run test validate verify lint clean

install:
	$(PY) -m pip install -e .

run:
	$(PY) run.py

test:
	$(PY) -m pytest tests/

validate:
	$(PY) -c "from bitsentry.audit_engine import AuditEngine; e=AuditEngine(); r=e.generate_audit_report(); print(r); e.export_html_report(); print('HTML report: validation/audit_report.html')"

verify:
	$(PY) -c "from bitsentry.audit_engine import AuditEngine; e=AuditEngine(); r=e.generate_audit_report(); v=e.verify_integrity(r['integrity_hash']); print('Integrity verified:', v)"

lint:
	$(PY) -m ruff check bitsentry/

clean:
	rm -rf dist/ build/ *.egg-info __pycache__
