# 🔒 Auto Backup Script

Backup profissional em Python com compressão, paralelismo, lockfile, retenção e logs.

## 🧠 Recursos

- Backup de múltiplas pastas
- Compactação automática em `.zip`
- Lockfile para evitar execuções simultâneas
- Suporte a `dry-run`
- Cópia paralela com `ThreadPoolExecutor`
- Retenção de backups antigos
- Logs para terminal e arquivo
- Configuração via YAML

## 📦 Requisitos

- Python 3.8+
- `pip install tqdm pyyaml`

## 🚀 Execução

```bash
python backup.py -c config.yaml
