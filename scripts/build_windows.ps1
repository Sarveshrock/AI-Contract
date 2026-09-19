# Build a distributable Windows folder. Run from the project root in an activated virtual environment.
$ErrorActionPreference = "Stop"
python -m pip install -r requirements-dev.txt
python scripts/generate_migrations.py --check
python -m pytest
pyinstaller ContractLens.spec --noconfirm
Write-Host "Built dist\ContractLens\ContractLens.exe. Copy .env.example to dist\ContractLens\.env and fill it in."
