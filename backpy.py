import os
import yaml
import zipfile
import time
import logging
import shutil
import argparse
import sys
from datetime import datetime
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Classe para o Mecanismo de Lock File ---
class LockFile:
    """Gerencia um arquivo de trava para evitar execuções simultâneas."""
    def __init__(self, path):
        self.path = path
        self.pid = os.getpid()

    def __enter__(self):
        if os.path.exists(self.path):
            raise RuntimeError(f"Lock file '{self.path}' já existe. Outro processo pode estar em execução.")
        with open(self.path, 'w') as f:
            f.write(str(self.pid))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if os.path.exists(self.path):
            os.remove(self.path)

# --- Classe Principal do Backup ---
class BackupRunner:
    """Encapsula toda a lógica e estado do processo de backup."""
    def __init__(self, config_path, dry_run=False):
        self.config = self._load_config(config_path)
        self.dry_run = dry_run
        self.start_time = time.time()
        self.staging_dir = self.config['staging_path']
        self.lock_path = os.path.join(self.config['destination'], '.backup.lock')
        self._setup_logging()

    def _load_config(self, path):
        """Carrega a configuração de um arquivo YAML."""
        logging.info(f"Carregando configuração de '{path}'")
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)

    def _setup_logging(self):
        """Configura o sistema de logging para arquivo e console."""
        log_path = os.path.join(self.config['destination'], "backup.log")
        os.makedirs(self.config['destination'], exist_ok=True)
        # Limpa handlers antigos para evitar logs duplicados
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_path, encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )

    def _get_files_to_backup(self):
        """Gera uma lista de todos os arquivos de origem a serem copiados."""
        for folder in self.config['source_folders']:
            if not os.path.isdir(folder):
                logging.warning(f"Pasta de origem não encontrada, pulando: {folder}")
                continue
            for root, _, files in os.walk(folder):
                for f in files:
                    yield os.path.join(root, f)

    def _copy_worker(self, src_file):
        """Trabalhador que copia um único arquivo e retorna seu tamanho."""
        file_size = 0
        try:
            file_size = os.path.getsize(src_file)
            relative_path = os.path.relpath(src_file, start=os.path.commonpath(self.config['source_folders']))
            dst_file = os.path.join(self.staging_dir, relative_path)
            os.makedirs(os.path.dirname(dst_file), exist_ok=True)
            if not self.dry_run:
                shutil.copy2(src_file, dst_file)
            return True, file_size, src_file
        except Exception as e:
            logging.error(f"Falha ao copiar '{src_file}': {e}")
            return False, 0, src_file

    def _copy_files_to_staging_parallel(self, files, total_copy_size):
        """Copia arquivos em paralelo, exibindo a velocidade em MB/s."""
        logging.info(f"Copiando {len(files)} arquivos ({total_copy_size / (1024*1024):.2f} MB) para staging com {self.config['parallel_workers']} workers...")
        if self.dry_run:
            logging.info("[DRY RUN] Simulação de cópia de arquivos.")
        
        with ThreadPoolExecutor(max_workers=self.config['parallel_workers']) as executor:
            with tqdm(total=total_copy_size, desc='Copiando arquivos', ncols=100, unit='B', unit_scale=True, unit_divisor=1024) as pbar:
                futures = [executor.submit(self._copy_worker, f) for f in files]
                for future in as_completed(futures):
                    success, size_processed, _ = future.result()
                    if success and size_processed > 0:
                        pbar.update(size_processed)

    def _zip_staging_folder(self, output_zip_path):
        """Compacta a pasta de staging com uma barra de progresso em file/s."""
        if self.dry_run:
            logging.info(f"[DRY RUN] Simulação de criação do arquivo: {output_zip_path}")
            return
            
        logging.info(f"Compactando staging para '{output_zip_path}'...")
        files_to_zip = [os.path.join(root, file) for root, _, files in os.walk(self.staging_dir) for file in files]
        
        with zipfile.ZipFile(output_zip_path, 'w', compression=zipfile.ZIP_DEFLATED) as zipf:
            with tqdm(total=len(files_to_zip), desc='Compactando arquivos', ncols=100, unit='file') as pbar:
                for file_path in files_to_zip:
                    arcname = os.path.relpath(file_path, self.staging_dir)
                    zipf.write(file_path, arcname)
                    pbar.update(1)
        logging.info("Compactação concluída.")

    def _cleanup(self):
        """Limpa a pasta de staging e os backups antigos conforme a retenção."""
        if os.path.exists(self.staging_dir):
            if self.dry_run:
                logging.info(f"[DRY RUN] Simulação de limpeza da pasta: {self.staging_dir}")
            else:
                logging.info(f"Limpando pasta de staging: {self.staging_dir}")
                shutil.rmtree(self.staging_dir)
        
        logging.info(f"Verificando backups antigos para retenção de {self.config['retention']} dias.")
        try:
            backup_dir = self.config['destination']
            prefix = self.config['prefix']
            backups = sorted(
                [f for f in os.listdir(backup_dir) if f.startswith(prefix) and f.endswith('.zip')],
                key=lambda f: os.path.getmtime(os.path.join(backup_dir, f))
            )
            to_delete = backups[:-self.config['retention']]
            for backup in to_delete:
                if self.dry_run:
                    logging.info(f"[DRY RUN] Simulação de exclusão do backup antigo: {backup}")
                else:
                    logging.info(f"Excluindo backup antigo: {backup}")
                    os.remove(os.path.join(backup_dir, backup))
        except Exception as e:
            logging.error(f"Erro durante a limpeza de backups antigos: {e}")

    def run(self):
        """Orquestra o processo completo de backup."""
        logging.info("================ INICIANDO PROCESSO DE BACKUP ================")
        try:
            with LockFile(self.lock_path):
                if os.path.exists(self.staging_dir):
                    shutil.rmtree(self.staging_dir)
                os.makedirs(self.staging_dir, exist_ok=True)
                
                files_to_backup = list(self._get_files_to_backup())
                if not files_to_backup:
                    logging.warning("Nenhum arquivo encontrado. Encerrando.")
                    return

                total_copy_size = sum(os.path.getsize(f) for f in files_to_backup)
                self._copy_files_to_staging_parallel(files_to_backup, total_copy_size)
                
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_filename = f"{self.config['prefix']}_{timestamp}.zip"
                backup_path = os.path.join(self.config['destination'], backup_filename)
                self._zip_staging_folder(backup_path)
        except RuntimeError as e:
            logging.error(f"Erro de execução: {e}")
        except Exception as e:
            logging.critical("Ocorreu um erro fatal no processo de backup.", exc_info=True)
        finally:
            self._cleanup()
            elapsed = time.time() - self.start_time
            logging.info(f"Tempo total do processo: {elapsed:.2f} segundos.")
            logging.info("================ PROCESSO DE BACKUP FINALIZADO ================\n")

def main():
    """Ponto de entrada do script, lida com argumentos da linha de comando."""
    parser = argparse.ArgumentParser(
        description="Script de Backup Profissional.",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-c", "--config", 
        default="config.yaml", 
        help="Caminho para o arquivo de configuração YAML.\n(Padrão: config.yaml)"
    )
    parser.add_argument(
        "--dry-run", 
        action="store_true", 
        help="Executa o script em modo de simulação, sem alterar arquivos."
    )
    args = parser.parse_args()

    try:
        runner = BackupRunner(config_path=args.config, dry_run=args.dry_run)
        runner.run()
    except FileNotFoundError:
        logging.error(f"Arquivo de configuração '{args.config}' não encontrado.")
    except Exception as e:
        logging.critical(f"Falha ao iniciar o BackupRunner: {e}", exc_info=True)
    finally:
        input("Processo finalizado. Pressione Enter para sair...")

if __name__ == "__main__":
    main()