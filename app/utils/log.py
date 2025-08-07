import os
import logging
from logging.handlers import RotatingFileHandler

def get_logger(name: str = "app_logger", log_dir: str = "logs", log_file: str = "app.log") -> logging.Logger:
    """
    Crea (o reutiliza) un logger con rotación de archivos.
    Evita duplicar handlers si se llama múltiples veces.
    """
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger  # ya configurado

    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        os.path.join(log_dir, log_file),
        maxBytes=1_000_000,
        backupCount=5
    )
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
