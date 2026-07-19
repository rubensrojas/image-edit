#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, QThreadPool, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QCloseEvent, QDesktopServices, QDragEnterEvent, QDropEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from sre_processor import (
    SUPPORTED_EXTENSIONS,
    collision_safe_path,
    edit_image,
    is_supported_file,
    resource_root,
    tesseract_version,
    unique_paths,
)


APP_NAME = "SRE Image Editor"
ACCENT = "#D4AF37"


class PaddedSpinBox(QSpinBox):
    def textFromValue(self, value: int) -> str:
        return f"{value:03d}"

    def valueFromText(self, text: str) -> int:
        try:
            return int(text)
        except ValueError:
            return self.minimum()


class DropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(112)
        self.setAccessibleName("Área para arrastar imagens")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Arraste as imagens aqui")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel("JPG, PNG ou WebP · Selecione vários arquivos")
        detail.setObjectName("muted")
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addWidget(detail)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragActive", True)
            self.style().unpolish(self)
            self.style().polish(self)

    def dragLeaveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._clear_drag_state()
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self._clear_drag_state()
        self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def _clear_drag_state(self) -> None:
        self.setProperty("dragActive", False)
        self.style().unpolish(self)
        self.style().polish(self)


class WorkerSignals(QObject):
    current = Signal(str)
    progress = Signal(int, int)
    finished = Signal(int, object)


class BatchWorker(QRunnable):
    def __init__(self, files: list[Path], output_dir: Path, sre_number: int) -> None:
        super().__init__()
        self.files = files
        self.output_dir = output_dir
        self.sre_number = sre_number
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        errors: list[tuple[str, str]] = []
        completed = 0
        total = len(self.files)
        for index, input_path in enumerate(self.files, start=1):
            self.signals.current.emit(input_path.name)
            try:
                output_path = collision_safe_path(self.output_dir, input_path.name)
                edit_image(input_path, output_path, self.sre_number)
                completed += 1
            except Exception as exc:  # Keep the rest of the batch moving.
                errors.append((input_path.name, str(exc)))
            self.signals.progress.emit(index, total)
        self.signals.finished.emit(completed, errors)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.files: list[Path] = []
        self.processing = False
        self.output_dir: Path | None = None
        self.settings = QSettings("SRE Tools", APP_NAME)
        self.thread_pool = QThreadPool.globalInstance()

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(660, 680)
        self.resize(700, 720)
        icon_path = resource_root() / "resources" / "app_icon.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(34, 30, 34, 30)
        layout.setSpacing(18)

        eyebrow = QLabel("EDIÇÃO DE IMAGENS EM LOTE")
        eyebrow.setObjectName("eyebrow")
        title = QLabel(APP_NAME)
        title.setObjectName("title")
        subtitle = QLabel("Altere o número SRE de várias imagens de uma só vez.")
        subtitle.setObjectName("muted")
        layout.addWidget(eyebrow)
        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.drop_zone = DropZone()
        self.drop_zone.files_dropped.connect(self.add_files)
        layout.addWidget(self.drop_zone)

        file_header = QHBoxLayout()
        self.file_count = QLabel("Nenhuma imagem selecionada")
        self.file_count.setObjectName("sectionLabel")
        select_button = QPushButton("Selecionar imagens")
        select_button.setObjectName("secondaryButton")
        select_button.clicked.connect(self.select_files)
        file_header.addWidget(self.file_count)
        file_header.addStretch()
        file_header.addWidget(select_button)
        layout.addLayout(file_header)

        self.file_list = QListWidget()
        self.file_list.setObjectName("fileList")
        self.file_list.setMaximumHeight(125)
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.file_list.setAccessibleName("Imagens selecionadas")
        layout.addWidget(self.file_list)

        file_actions = QHBoxLayout()
        self.feedback = QLabel("")
        self.feedback.setObjectName("muted")
        self.feedback.setWordWrap(True)
        remove_button = QPushButton("Remover selecionadas")
        remove_button.setObjectName("textButton")
        remove_button.clicked.connect(self.remove_selected)
        clear_button = QPushButton("Limpar")
        clear_button.setObjectName("textButton")
        clear_button.clicked.connect(self.clear_files)
        file_actions.addWidget(self.feedback, 1)
        file_actions.addWidget(remove_button)
        file_actions.addWidget(clear_button)
        layout.addLayout(file_actions)

        controls = QHBoxLayout()
        controls.setSpacing(14)
        number_column = QVBoxLayout()
        number_label = QLabel("Número SRE")
        number_label.setObjectName("sectionLabel")
        self.sre_number = PaddedSpinBox()
        self.sre_number.setRange(0, 999)
        self.sre_number.setValue(10)
        self.sre_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sre_number.setAccessibleName("Número SRE")
        number_column.addWidget(number_label)
        number_column.addWidget(self.sre_number)

        output_column = QVBoxLayout()
        output_label = QLabel("Pasta de destino")
        output_label.setObjectName("sectionLabel")
        output_row = QHBoxLayout()
        self.output_field = QLineEdit()
        self.output_field.setReadOnly(True)
        self.output_field.setPlaceholderText("Escolha onde salvar as imagens editadas")
        self.output_field.setAccessibleName("Pasta de destino")
        browse_button = QPushButton("Escolher")
        browse_button.setObjectName("secondaryButton")
        browse_button.clicked.connect(self.choose_output_folder)
        output_row.addWidget(self.output_field, 1)
        output_row.addWidget(browse_button)
        output_column.addWidget(output_label)
        output_column.addLayout(output_row)

        controls.addLayout(number_column)
        controls.addLayout(output_column, 1)
        layout.addLayout(controls)

        saved_output = self.settings.value("outputDirectory", "", str)
        if saved_output and Path(saved_output).is_dir():
            self.set_output_folder(Path(saved_output))

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Progresso da edição")
        self.progress.hide()
        layout.addWidget(self.progress)

        self.status = QLabel("Selecione as imagens para começar.")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        bottom = QHBoxLayout()
        self.open_output_button = QPushButton("Abrir pasta de destino")
        self.open_output_button.setObjectName("secondaryButton")
        self.open_output_button.clicked.connect(self.open_output_folder)
        self.open_output_button.hide()
        self.process_button = QPushButton("Editar imagens")
        self.process_button.setObjectName("primaryButton")
        self.process_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.process_button.clicked.connect(self.start_processing)
        bottom.addWidget(self.open_output_button)
        bottom.addStretch()
        bottom.addWidget(self.process_button)
        layout.addLayout(bottom)

        self.refresh_state()

    @Slot()
    def select_files(self) -> None:
        patterns = " ".join(f"*{extension}" for extension in sorted(SUPPORTED_EXTENSIONS))
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecionar imagens",
            "",
            f"Imagens ({patterns});;Todos os arquivos (*)",
        )
        self.add_files([Path(filename) for filename in filenames])

    @Slot(list)
    def add_files(self, candidates: list[Path]) -> None:
        supported = unique_paths(candidates)
        existing = {str(path).casefold() for path in self.files}
        additions = [path for path in supported if str(path).casefold() not in existing]
        unsupported_count = sum(1 for path in candidates if not is_supported_file(path))
        supported_candidate_count = sum(1 for path in candidates if is_supported_file(path))
        duplicate_count = supported_candidate_count - len(additions)
        self.files.extend(additions)

        for path in additions:
            self.file_list.addItem(path.name)
            item = self.file_list.item(self.file_list.count() - 1)
            item.setToolTip(str(path))

        notes = []
        if additions:
            count = len(additions)
            notes.append(f"{count} imagem{' adicionada' if count == 1 else 's adicionadas'}")
        if duplicate_count:
            notes.append(
                f"{duplicate_count} duplicada{' ignorada' if duplicate_count == 1 else 's ignoradas'}"
            )
        if unsupported_count:
            notes.append(
                f"{unsupported_count} item{' incompatível ignorado' if unsupported_count == 1 else 's incompatíveis ignorados'}"
            )
        self.feedback.setText(" · ".join(notes))
        self.refresh_state()

    @Slot()
    def remove_selected(self) -> None:
        rows = sorted({index.row() for index in self.file_list.selectedIndexes()}, reverse=True)
        for row in rows:
            self.file_list.takeItem(row)
            del self.files[row]
        count = len(rows)
        self.feedback.setText(
            f"{count} imagem{' removida' if count == 1 else 's removidas'}"
            if rows
            else "Selecione as imagens que deseja remover"
        )
        self.refresh_state()

    @Slot()
    def clear_files(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.feedback.clear()
        self.refresh_state()

    @Slot()
    def choose_output_folder(self) -> None:
        start = str(self.output_dir or Path.home())
        selected = QFileDialog.getExistingDirectory(self, "Escolher pasta de destino", start)
        if selected:
            self.set_output_folder(Path(selected))
            self.refresh_state()

    def set_output_folder(self, directory: Path) -> None:
        self.output_dir = directory.expanduser().resolve()
        self.output_field.setText(str(self.output_dir))
        self.output_field.setToolTip(str(self.output_dir))
        self.settings.setValue("outputDirectory", str(self.output_dir))

    def refresh_state(self) -> None:
        count = len(self.files)
        self.file_count.setText(
            f"{count} imagem selecionada" if count == 1 else
            f"{count} imagens selecionadas" if count else
            "Nenhuma imagem selecionada"
        )
        self.process_button.setEnabled(bool(self.files and self.output_dir) and not self.processing)

    @Slot()
    def start_processing(self) -> None:
        if not self.files or self.output_dir is None:
            return
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Não foi possível usar a pasta de destino",
                f"Escolha outra pasta e tente novamente.\n\n{exc}",
            )
            return

        self.processing = True
        self.open_output_button.hide()
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("Preparando as imagens…")
        self.process_button.setText("Editando…")
        self.refresh_state()

        worker = BatchWorker(list(self.files), self.output_dir, self.sre_number.value())
        worker.signals.current.connect(lambda name: self.status.setText(f"Editando {name}"))
        worker.signals.progress.connect(self.update_progress)
        worker.signals.finished.connect(self.processing_finished)
        self.thread_pool.start(worker)

    @Slot(int, int)
    def update_progress(self, completed: int, total: int) -> None:
        self.progress.setValue(round(completed / total * 100))

    @Slot(int, object)
    def processing_finished(self, completed: int, errors: object) -> None:
        failures = list(errors)
        self.processing = False
        self.process_button.setText("Editar imagens")
        self.open_output_button.show()
        self.refresh_state()
        if failures:
            names = ", ".join(name for name, _ in failures[:3])
            remainder = len(failures) - 3
            extra = f" e mais {remainder}" if remainder > 0 else ""
            self.status.setText(
                f"Concluído: {completed} editada(s) e {len(failures)} com falha ({names}{extra})."
            )
            details = "\n\n".join(f"{name}\n{message}" for name, message in failures)
            QMessageBox.warning(self, "Algumas imagens não foram editadas", details)
        else:
            self.status.setText(
                f"Concluído. {completed} imagem editada com sucesso."
                if completed == 1
                else f"Concluído. {completed} imagens editadas com sucesso."
            )

    @Slot()
    def open_output_folder(self) -> None:
        if self.output_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.output_dir)))

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.processing:
            QMessageBox.information(
                self,
                "Edição em andamento",
                "Aguarde a conclusão deste lote antes de fechar o aplicativo.",
            )
            event.ignore()
            return
        event.accept()


STYLESHEET = f"""
QWidget#root {{ background: #151515; color: #F5F3EC; }}
QWidget {{ font-size: 14px; color: #F5F3EC; }}
QLabel#eyebrow {{ color: {ACCENT}; font-size: 11px; font-weight: 700; letter-spacing: 2px; }}
QLabel#title {{ font-size: 29px; font-weight: 700; }}
QLabel#muted {{ color: #AAA79F; }}
QLabel#sectionLabel {{ color: #E8E5DC; font-weight: 600; }}
QLabel#status {{ color: #C9C5BB; }}
QFrame#dropZone {{ background: #1D1D1D; border: 1px dashed #5C5A55; border-radius: 12px; }}
QFrame#dropZone[dragActive="true"] {{ background: #28251C; border: 2px solid {ACCENT}; }}
QLabel#dropTitle {{ font-size: 17px; font-weight: 650; }}
QListWidget#fileList, QLineEdit, QSpinBox {{
    background: #1D1D1D; border: 1px solid #393939; border-radius: 8px;
    padding: 9px; selection-background-color: #4A4020; selection-color: #FFFFFF;
}}
QListWidget#fileList:focus, QLineEdit:focus, QSpinBox:focus {{ border: 2px solid {ACCENT}; }}
QListWidget::item {{ padding: 5px 6px; border-radius: 4px; }}
QPushButton {{ min-height: 38px; padding: 0 15px; border-radius: 8px; font-weight: 600; }}
QPushButton#primaryButton {{ background: {ACCENT}; color: #17140B; border: 1px solid {ACCENT}; min-width: 120px; }}
QPushButton#primaryButton:hover {{ background: #E1C35D; }}
QPushButton#primaryButton:pressed {{ background: #B9962F; }}
QPushButton#primaryButton:disabled {{ background: #4A463A; color: #89857A; border-color: #4A463A; }}
QPushButton#secondaryButton {{ background: #242424; color: #F5F3EC; border: 1px solid #464646; }}
QPushButton#secondaryButton:hover {{ background: #303030; border-color: #686868; }}
QPushButton#textButton {{ background: transparent; color: #BBB7AD; border: 1px solid transparent; padding: 0 8px; }}
QPushButton#textButton:hover {{ color: #FFFFFF; background: #242424; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; }}
QProgressBar {{ background: #252525; border: 0; border-radius: 3px; height: 6px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QToolTip {{ background: #292929; color: #FFFFFF; border: 1px solid #555555; padding: 5px; }}
"""


def main() -> int:
    if "--smoke-test" in sys.argv:
        print(f"{APP_NAME}: OK (Tesseract {tesseract_version()})")
        return 0
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("SRE Tools")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
