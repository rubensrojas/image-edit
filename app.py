#!/usr/bin/env python3
from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import zipfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPointF,
    QRunnable,
    QRect,
    QRectF,
    QSettings,
    QThreadPool,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QDesktopServices,
    QDragEnterEvent,
    QDropEvent,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QGridLayout,
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
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from sre_processor import (
    MAX_EDITED_FILENAME_BYTES,
    SUPPORTED_EXTENSIONS,
    collision_safe_path,
    edit_image,
    edited_filename,
    encode_edited_image,
    is_supported_file,
    read_raw_line_3,
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
        self.setMinimumHeight(170)
        self.setAccessibleName("Área para arrastar imagens")

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title = QLabel("Arraste as imagens aqui")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel("JPG, PNG ou WebP · Você pode selecionar vários arquivos")
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


def widget_animations_enabled(widget: QWidget) -> bool:
    return bool(
        widget.style().styleHint(
            QStyle.StyleHint.SH_Widget_Animate,
            None,
            widget,
        )
    )


class LoadingOverlay(QWidget):
    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.angle = 0
        self.motion_enabled_override: bool | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(40)
        self.timer.timeout.connect(self._advance)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAccessibleName("Atualizando prévia")
        self.hide()

    def start(self) -> None:
        self.show()
        self.raise_()
        if self.motion_enabled():
            self.timer.start()
        else:
            self.timer.stop()
            self.angle = 35
        self.update()

    def stop(self) -> None:
        self.timer.stop()
        self.hide()

    def motion_enabled(self) -> bool:
        if self.motion_enabled_override is not None:
            return self.motion_enabled_override
        return widget_animations_enabled(self)

    @Slot()
    def _advance(self) -> None:
        self.angle = (self.angle + 12) % 360
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 115))

        radius = 16
        arc = QRectF(
            self.width() / 2 - radius,
            self.height() / 2 - radius,
            radius * 2,
            radius * 2,
        )
        pen = QPen(QColor(ACCENT), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawArc(arc, -self.angle * 16, 270 * 16)


class DirectionArrow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setFixedWidth(52)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.setAccessibleName("Da imagem original para a imagem editada")

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(ACCENT), 2.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        center_y = self.height() / 2
        start_x = 7.0
        end_x = self.width() - 7.0
        painter.drawLine(QPointF(start_x, center_y), QPointF(end_x, center_y))
        painter.drawLine(
            QPointF(end_x, center_y), QPointF(end_x - 9, center_y - 8)
        )
        painter.drawLine(
            QPointF(end_x, center_y), QPointF(end_x - 9, center_y + 8)
        )


class PreviewImage(QLabel):
    """High-resolution preview with an overlay fitted to the rendered image."""

    def __init__(
        self,
        accessible_name: str,
        alignment: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignCenter,
    ) -> None:
        super().__init__()
        self._source = QPixmap()
        self._image_rect = QRect()
        self._horizontal_alignment = alignment
        self.setObjectName("previewImage")
        self.setAlignment(alignment | Qt.AlignmentFlag.AlignVCenter)
        self.setMinimumSize(260, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAccessibleName(accessible_name)
        self.setText("Preparando imagem…")
        self.loading_overlay = LoadingOverlay(self)

    @property
    def image_rect(self) -> QRect:
        return QRect(self._image_rect)

    @property
    def is_loading(self) -> bool:
        return not self.loading_overlay.isHidden()

    def set_source(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._refresh_pixmap()

    def clear_source(self, message: str = "Preparando imagem…") -> None:
        self.loading_overlay.stop()
        self._source = QPixmap()
        self._image_rect = QRect()
        super().clear()
        self.setText(message)

    def set_loading(self, loading: bool) -> None:
        if loading and not self._image_rect.isEmpty():
            self.loading_overlay.setGeometry(self._image_rect)
            self.loading_overlay.start()
        else:
            self.loading_overlay.stop()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._source.isNull():
            return
        content = self.contentsRect()
        scaled = self._source.scaled(
            content.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        if self._horizontal_alignment == Qt.AlignmentFlag.AlignLeft:
            x = content.left()
        elif self._horizontal_alignment == Qt.AlignmentFlag.AlignRight:
            x = content.right() - scaled.width() + 1
        else:
            x = content.left() + (content.width() - scaled.width()) // 2
        y = content.top() + (content.height() - scaled.height()) // 2
        self._image_rect = QRect(x, y, scaled.width(), scaled.height())
        super().setPixmap(scaled)
        if self.loading_overlay.isVisible():
            self.loading_overlay.setGeometry(self._image_rect)
            self.loading_overlay.raise_()


class AnimatedProgressBar(QProgressBar):
    target_reached = Signal(int)

    def __init__(self) -> None:
        super().__init__()
        self.motion_enabled_override: bool | None = None
        self._target = 0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(320)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(
            lambda value: QProgressBar.setValue(self, round(float(value)))
        )
        self.animation.finished.connect(self._animation_finished)

    def motion_enabled(self) -> bool:
        if self.motion_enabled_override is not None:
            return self.motion_enabled_override
        return widget_animations_enabled(self)

    def set_immediate_value(self, value: int) -> None:
        self.animation.stop()
        self._target = value
        QProgressBar.setValue(self, value)

    def set_animated_value(self, value: int) -> None:
        value = max(self.minimum(), min(self.maximum(), value))
        self._target = value
        if not self.motion_enabled() or value == self.value():
            self.set_immediate_value(value)
            self.target_reached.emit(value)
            return
        start = self.value()
        self.animation.stop()
        self.animation.setStartValue(start)
        self.animation.setEndValue(value)
        self.animation.start()

    def stop_animation(self) -> None:
        self.animation.stop()

    @Slot()
    def _animation_finished(self) -> None:
        QProgressBar.setValue(self, self._target)
        self.target_reached.emit(self._target)


class WorkerSignals(QObject):
    current = Signal(str)
    progress = Signal(int, int)
    finished = Signal(int, object)
    cancelled = Signal()


class BatchWorker(QRunnable):
    def __init__(
        self,
        files: list[Path],
        output_dir: Path,
        sre_number: int,
        *,
        full_line_text: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        super().__init__()
        self.files = files
        self.output_dir = output_dir
        self.sre_number = sre_number
        self.full_line_text = full_line_text
        self.cancel_event = cancel_event or threading.Event()
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        errors: list[tuple[str, str]] = []
        completed = 0
        total = len(self.files)
        for index, input_path in enumerate(self.files, start=1):
            if self.cancel_event.is_set():
                self.signals.cancelled.emit()
                return
            self.signals.current.emit(input_path.name)
            try:
                output_name = edited_filename(
                    input_path.name,
                    self.sre_number,
                    full_line_text=self.full_line_text,
                )
                output_path = collision_safe_path(
                    self.output_dir,
                    output_name,
                    max_filename_bytes=MAX_EDITED_FILENAME_BYTES,
                )
                if self.full_line_text is None:
                    edit_image(input_path, output_path, self.sre_number)
                else:
                    edit_image(
                        input_path,
                        output_path,
                        self.sre_number,
                        full_line_text=self.full_line_text,
                    )
                completed += 1
            except Exception as exc:  # Keep scanning so the user sees every failure.
                errors.append((input_path.name, str(exc)))
            self.signals.progress.emit(index, total)
        if self.cancel_event.is_set():
            self.signals.cancelled.emit()
            return
        self.signals.finished.emit(completed, errors)


class PreviewSignals(QObject):
    ready = Signal(int, bytes, str)
    failed = Signal(int, str, str)


class PreviewWorker(QRunnable):
    def __init__(
        self,
        request_id: int,
        input_path: Path,
        sre_number: int,
        full_line_text: str | None,
        read_raw_text: bool,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.input_path = input_path
        self.sre_number = sre_number
        self.full_line_text = full_line_text
        self.read_raw_text = read_raw_text
        self.signals = PreviewSignals()

    @Slot()
    def run(self) -> None:
        raw_text = ""
        if self.read_raw_text:
            try:
                with Image.open(self.input_path) as source:
                    raw_text = read_raw_line_3(source.convert("RGB"))
            except Exception:
                raw_text = ""
        try:
            encoded = encode_edited_image(
                self.input_path,
                self.sre_number,
                full_line_text=self.full_line_text,
            )
            self.signals.ready.emit(self.request_id, encoded, raw_text)
        except Exception as exc:
            self.signals.failed.emit(self.request_id, str(exc), raw_text)


class ExportSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(bool, str, str)


class ExportWorker(QRunnable):
    def __init__(self, files: list[Path], mode: str, destination: Path) -> None:
        super().__init__()
        self.files = files
        self.mode = mode
        self.destination = destination
        self.signals = ExportSignals()

    @Slot()
    def run(self) -> None:
        try:
            if self.mode == "folder":
                self.destination.mkdir(parents=True, exist_ok=True)
                total = len(self.files)
                for index, source in enumerate(self.files, start=1):
                    target = collision_safe_path(
                        self.destination,
                        source.name,
                        max_filename_bytes=MAX_EDITED_FILENAME_BYTES,
                    )
                    shutil.copy2(source, target)
                    self.signals.progress.emit(index, total)
                open_location = self.destination
            else:
                self.destination.parent.mkdir(parents=True, exist_ok=True)
                total = len(self.files)
                with zipfile.ZipFile(
                    self.destination, "w", compression=zipfile.ZIP_DEFLATED
                ) as archive:
                    for index, source in enumerate(self.files, start=1):
                        archive.write(source, arcname=source.name)
                        self.signals.progress.emit(index, total)
                open_location = self.destination.parent
            self.signals.finished.emit(True, str(open_location), "")
        except Exception as exc:
            if self.mode == "zip":
                try:
                    self.destination.unlink(missing_ok=True)
                except OSError:
                    pass
            self.signals.finished.emit(False, "", str(exc))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.files: list[Path] = []
        self.processing = False
        self.exporting = False
        self.temp_dir: Path | None = None
        self.output_files: list[Path] = []
        self.last_export_location: Path | None = None
        self.has_exported = False
        self.failed_names: set[str] = set()
        self.settings = QSettings("SRE Tools", APP_NAME)
        self.thread_pool = QThreadPool.globalInstance()
        self.cancel_event: threading.Event | None = None
        self.batch_worker: BatchWorker | None = None
        self.export_worker: ExportWorker | None = None
        self.preview_request_id = 0
        self.preview_valid = False
        self.preview_loading = False
        self.preview_bytes = b""
        self.preview_path: Path | None = None
        self.raw_line_loaded = False
        self.full_text_touched = False
        self.closing_after_cancel = False
        self.pending_batch_result: tuple[int, list[tuple[str, str]]] | None = None

        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1040, 800)
        self.resize(1280, 960)
        icon_path = resource_root() / "resources" / "app_icon.svg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(32, 22, 32, 26)
        root_layout.setSpacing(13)

        eyebrow = QLabel("EDIÇÃO DE IMAGENS EM LOTE")
        eyebrow.setObjectName("eyebrow")
        title = QLabel(APP_NAME)
        title.setObjectName("appTitle")
        root_layout.addWidget(eyebrow)
        root_layout.addWidget(title)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.pages.addWidget(self._build_selection_page())
        self.pages.addWidget(self._build_edit_page())
        self.pages.addWidget(self._build_processing_page())
        self.pages.addWidget(self._build_result_page())
        root_layout.addWidget(self.pages, 1)

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(280)
        self.preview_timer.timeout.connect(self.start_preview)

        self.show_page(0)
        self.refresh_selection_state()

    def _page_heading(self, title: str, subtitle: str) -> tuple[QLabel, QLabel]:
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        detail = QLabel(subtitle)
        detail.setObjectName("muted")
        detail.setWordWrap(True)
        return heading, detail

    def _build_selection_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(16)
        heading, detail = self._page_heading(
            "Escolha as imagens",
            "Os arquivos originais serão preservados durante toda a edição.",
        )
        layout.addWidget(heading)
        layout.addWidget(detail)

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
        self.file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.file_list.setAccessibleName("Imagens selecionadas")
        layout.addWidget(self.file_list, 1)

        actions = QHBoxLayout()
        self.feedback = QLabel("")
        self.feedback.setObjectName("muted")
        self.feedback.setWordWrap(True)
        remove_button = QPushButton("Remover selecionadas")
        remove_button.setObjectName("textButton")
        remove_button.clicked.connect(self.remove_selected)
        clear_button = QPushButton("Limpar")
        clear_button.setObjectName("textButton")
        clear_button.clicked.connect(self.clear_files)
        actions.addWidget(self.feedback, 1)
        actions.addWidget(remove_button)
        actions.addWidget(clear_button)
        layout.addLayout(actions)

        footer = QHBoxLayout()
        footer.addStretch()
        self.continue_button = QPushButton("Continuar")
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.clicked.connect(self.continue_to_edit)
        footer.addWidget(self.continue_button)
        layout.addLayout(footer)
        return page

    def _build_edit_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(10)
        heading, detail = self._page_heading(
            "Configure a edição",
            "A prévia usa a primeira imagem e mostra exatamente como esse arquivo será salvo.",
        )
        heading_row = QHBoxLayout()
        self.preview_filename = QLabel("")
        self.preview_filename.setObjectName("muted")
        heading_row.addWidget(heading)
        heading_row.addStretch()
        heading_row.addWidget(self.preview_filename)
        layout.addLayout(heading_row)
        layout.addWidget(detail)

        controls = QHBoxLayout()
        controls.setSpacing(14)
        self.full_line_toggle = QCheckBox("Editar linha completa")
        self.full_line_toggle.setObjectName("modeToggle")
        self.full_line_toggle.setAccessibleName("Editar linha completa")
        self.full_line_toggle.toggled.connect(self.mode_changed)
        controls.addWidget(self.full_line_toggle)

        self.edit_inputs = QStackedWidget()
        self.edit_inputs.setObjectName("editInputs")
        number_page = QWidget()
        number_layout = QHBoxLayout(number_page)
        number_layout.setContentsMargins(0, 0, 0, 0)
        number_label = QLabel("Número SRE")
        number_label.setObjectName("sectionLabel")
        self.sre_number = PaddedSpinBox()
        self.sre_number.setRange(0, 999)
        self.sre_number.setValue(10)
        self.sre_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sre_number.setAccessibleName("Número SRE")
        self.sre_number.setFixedWidth(96)
        self.sre_number.valueChanged.connect(self.schedule_preview)
        number_layout.addWidget(number_label)
        number_layout.addWidget(self.sre_number)
        number_layout.addStretch()

        full_page = QWidget()
        full_layout = QVBoxLayout(full_page)
        full_layout.setContentsMargins(0, 0, 0, 0)
        full_layout.setSpacing(5)
        full_label = QLabel("Nova terceira linha")
        full_label.setObjectName("sectionLabel")
        self.full_line_input = QLineEdit()
        self.full_line_input.setAccessibleName("Texto completo da terceira linha")
        self.full_line_input.setPlaceholderText("Digite o texto que será aplicado ao lote")
        self.full_line_input.textEdited.connect(self.full_line_edited)
        self.full_line_message = QLabel(
            "O texto detectado é apenas um rascunho. Confira a prévia."
        )
        self.full_line_message.setObjectName("fieldMessage")
        self.full_line_message.setWordWrap(True)
        full_layout.addWidget(full_label)
        full_layout.addWidget(self.full_line_input)
        full_layout.addWidget(self.full_line_message)

        self.edit_inputs.addWidget(number_page)
        self.edit_inputs.addWidget(full_page)
        controls.addWidget(self.edit_inputs, 1)
        layout.addLayout(controls)

        comparison = QGridLayout()
        comparison.setHorizontalSpacing(14)
        comparison.setVerticalSpacing(6)
        before_label = QLabel("ANTES")
        before_label.setObjectName("previewCaption")
        before_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.before_preview = PreviewImage(
            "Imagem original", Qt.AlignmentFlag.AlignRight
        )
        after_label = QLabel("DEPOIS")
        after_label.setObjectName("previewCaption")
        after_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self.after_preview = PreviewImage(
            "Prévia da imagem editada", Qt.AlignmentFlag.AlignLeft
        )
        self.preview_arrow = DirectionArrow()
        comparison.addWidget(before_label, 0, 0)
        comparison.addWidget(after_label, 0, 2)
        comparison.addWidget(self.before_preview, 1, 0)
        comparison.addWidget(self.preview_arrow, 1, 1)
        comparison.addWidget(self.after_preview, 1, 2)
        comparison.setColumnStretch(0, 1)
        comparison.setColumnStretch(2, 1)
        comparison.setRowStretch(1, 1)
        layout.addLayout(comparison, 1)

        self.preview_status = QLabel("")
        self.preview_status.setObjectName("previewStatus")
        self.preview_status.setWordWrap(True)
        layout.addWidget(self.preview_status)

        footer = QHBoxLayout()
        back_button = QPushButton("Voltar")
        back_button.setObjectName("secondaryButton")
        back_button.clicked.connect(lambda: self.show_page(0))
        self.apply_button = QPushButton("Aplicar edição em todas as imagens")
        self.apply_button.setObjectName("primaryButton")
        self.apply_button.clicked.connect(self.start_processing)
        footer.addWidget(back_button)
        footer.addStretch()
        footer.addWidget(self.apply_button)
        layout.addLayout(footer)
        return page

    def _build_processing_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(80, 80, 80, 80)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(18)
        heading = QLabel("Editando as imagens")
        heading.setObjectName("processingTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.processing_count = QLabel("Preparando o lote…")
        self.processing_count.setObjectName("processingCount")
        self.processing_count.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.processing_filename = QLabel("")
        self.processing_filename.setObjectName("muted")
        self.processing_filename.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.processing_filename.setWordWrap(True)
        self.progress = AnimatedProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName("Progresso da edição")
        self.progress.setMinimumWidth(440)
        self.progress.target_reached.connect(self.progress_target_reached)
        self.cancel_button = QPushButton("Cancelar edição")
        self.cancel_button.setObjectName("secondaryButton")
        self.cancel_button.clicked.connect(self.cancel_processing)
        layout.addStretch()
        layout.addWidget(heading)
        layout.addWidget(self.processing_count)
        layout.addWidget(self.processing_filename)
        layout.addWidget(self.progress)
        layout.addWidget(self.cancel_button, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addStretch()
        return page

    def _build_result_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(54, 46, 54, 40)
        layout.setSpacing(16)
        self.result_kicker = QLabel("LOTE CONCLUÍDO")
        self.result_kicker.setObjectName("eyebrow")
        self.result_title = QLabel("")
        self.result_title.setObjectName("resultTitle")
        self.result_summary = QLabel("")
        self.result_summary.setObjectName("muted")
        self.result_summary.setWordWrap(True)
        layout.addWidget(self.result_kicker)
        layout.addWidget(self.result_title)
        layout.addWidget(self.result_summary)

        self.result_error_list = QListWidget()
        self.result_error_list.setObjectName("errorList")
        self.result_error_list.setAccessibleName("Erros do processamento")
        self.result_error_list.hide()
        layout.addWidget(self.result_error_list, 1)

        self.result_export_progress = QProgressBar()
        self.result_export_progress.setRange(0, 100)
        self.result_export_progress.setTextVisible(False)
        self.result_export_progress.hide()
        self.result_export_status = QLabel("")
        self.result_export_status.setObjectName("previewStatus")
        self.result_export_status.setWordWrap(True)
        layout.addWidget(self.result_export_progress)
        layout.addWidget(self.result_export_status)
        layout.addStretch()

        self.export_actions = QWidget()
        export_layout = QHBoxLayout(self.export_actions)
        export_layout.setContentsMargins(0, 0, 0, 0)
        self.save_folder_button = QPushButton("Salvar em uma pasta")
        self.save_folder_button.setObjectName("primaryButton")
        self.save_folder_button.clicked.connect(self.choose_export_folder)
        self.save_zip_button = QPushButton("Salvar como ZIP")
        self.save_zip_button.setObjectName("secondaryButton")
        self.save_zip_button.clicked.connect(self.choose_export_zip)
        export_layout.addWidget(self.save_folder_button)
        export_layout.addWidget(self.save_zip_button)
        export_layout.addStretch()
        layout.addWidget(self.export_actions)

        footer = QHBoxLayout()
        self.review_button = QPushButton("Revisar imagens")
        self.review_button.setObjectName("primaryButton")
        self.review_button.clicked.connect(self.review_failed_images)
        self.review_button.hide()
        self.open_output_button = QPushButton("Abrir pasta")
        self.open_output_button.setObjectName("secondaryButton")
        self.open_output_button.clicked.connect(self.open_output_folder)
        self.open_output_button.hide()
        self.new_batch_button = QPushButton("Editar outro lote")
        self.new_batch_button.setObjectName("textButton")
        self.new_batch_button.clicked.connect(self.start_new_batch)
        footer.addWidget(self.review_button)
        footer.addWidget(self.open_output_button)
        footer.addStretch()
        footer.addWidget(self.new_batch_button)
        layout.addLayout(footer)
        return page

    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)

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
                f"{duplicate_count} duplicada"
                f"{' ignorada' if duplicate_count == 1 else 's ignoradas'}"
            )
        if unsupported_count:
            notes.append(
                f"{unsupported_count} item"
                f"{' incompatível ignorado' if unsupported_count == 1 else 's incompatíveis ignorados'}"
            )
        self.feedback.setText(" · ".join(notes))
        self.refresh_selection_state()

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
        self.refresh_selection_state()

    @Slot()
    def clear_files(self) -> None:
        self.files.clear()
        self.file_list.clear()
        self.feedback.clear()
        self.refresh_selection_state()

    def refresh_selection_state(self) -> None:
        count = len(self.files)
        self.file_count.setText(
            f"{count} imagem selecionada"
            if count == 1
            else f"{count} imagens selecionadas"
            if count
            else "Nenhuma imagem selecionada"
        )
        self.continue_button.setEnabled(bool(self.files))

    @Slot()
    def continue_to_edit(self) -> None:
        if not self.files:
            return
        input_path = self.files[0]
        preview_changed = input_path != self.preview_path
        if preview_changed:
            self.preview_path = input_path
            self.raw_line_loaded = False
            if not self.full_text_touched:
                self.full_line_input.clear()
            self.preview_bytes = b""
            self.after_preview.clear_source()
        before = QPixmap(str(input_path))
        if before.isNull():
            self.before_preview.clear_source("Não foi possível abrir esta imagem")
            self.after_preview.clear_source("Não foi possível abrir esta imagem")
        else:
            self.before_preview.set_source(before)
            if preview_changed:
                self.after_preview.set_source(before)
        self.preview_filename.setText(input_path.name)
        self.show_page(1)
        self.schedule_preview(immediate=True)

    @Slot(bool)
    def mode_changed(self, full_line: bool) -> None:
        self.edit_inputs.setCurrentIndex(1 if full_line else 0)
        self.schedule_preview(immediate=full_line and not self.raw_line_loaded)

    @Slot(str)
    def full_line_edited(self, _text: str) -> None:
        self.full_text_touched = True
        self.schedule_preview()

    @Slot()
    @Slot(int)
    def schedule_preview(
        self, _value: object = None, *, immediate: bool = False
    ) -> None:
        if self.pages.currentIndex() != 1 or not self.files:
            return
        self.preview_request_id += 1
        self.preview_valid = False
        self.preview_loading = True
        self.apply_button.setEnabled(False)
        self.after_preview.set_loading(True)
        self._set_preview_status("Atualizando prévia…")
        self.preview_timer.start(0 if immediate else 280)

    @Slot()
    def start_preview(self) -> None:
        if not self.files:
            return
        full_line_text = None
        if self.full_line_toggle.isChecked():
            full_line_text = self.full_line_input.text().strip()
            if not full_line_text:
                if self.raw_line_loaded:
                    self.preview_loading = False
                    self.preview_valid = False
                    self.after_preview.set_loading(False)
                    self._set_full_line_message(
                        "Digite o texto que será aplicado à terceira linha.", error=True
                    )
                    self._set_preview_status("A prévia será atualizada quando houver texto.")
                    self.apply_button.setEnabled(False)
                    return
                full_line_text = None

        worker = PreviewWorker(
            self.preview_request_id,
            self.files[0],
            self.sre_number.value(),
            full_line_text,
            not self.raw_line_loaded,
        )
        worker.signals.ready.connect(self.preview_ready)
        worker.signals.failed.connect(self.preview_failed)
        self.thread_pool.start(worker)

    @Slot(int, bytes, str)
    def preview_ready(self, request_id: int, encoded: bytes, raw_text: str) -> None:
        if request_id != self.preview_request_id:
            return
        if self._apply_raw_line(raw_text) and self.full_line_toggle.isChecked():
            self.schedule_preview(immediate=True)
            return
        if self.full_line_toggle.isChecked() and not self.full_line_input.text().strip():
            self.preview_loading = False
            self.preview_valid = False
            self.after_preview.set_loading(False)
            self._set_full_line_message(
                "Não foi possível detectar o texto. Digite a terceira linha manualmente.",
                error=True,
            )
            self._set_preview_status("A prévia será atualizada quando houver texto.")
            self.apply_button.setEnabled(False)
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(encoded):
            self.preview_failed(request_id, "Não foi possível exibir a prévia gerada", raw_text)
            return
        self.preview_bytes = encoded
        self.after_preview.set_source(pixmap)
        self.after_preview.set_loading(False)
        self.preview_loading = False
        self.preview_valid = True
        self._set_preview_status("Prévia atualizada.")
        if self.full_line_toggle.isChecked():
            self._set_full_line_message(
                "A linha cabe sem reduzir a fonte. Confira o resultado na imagem."
            )
        self.apply_button.setEnabled(bool(self.files))

    @Slot(int, str, str)
    def preview_failed(self, request_id: int, message: str, raw_text: str) -> None:
        if request_id != self.preview_request_id:
            return
        if self._apply_raw_line(raw_text) and self.full_line_toggle.isChecked():
            self.schedule_preview(immediate=True)
            return
        self.preview_loading = False
        self.preview_valid = False
        self.after_preview.set_loading(False)
        self._set_preview_status(message, error=True)
        if self.full_line_toggle.isChecked():
            self._set_full_line_message(message, error=True)
        self.apply_button.setEnabled(False)

    def _apply_raw_line(self, raw_text: str) -> bool:
        if self.raw_line_loaded:
            return False
        self.raw_line_loaded = True
        if not self.full_text_touched and raw_text:
            self.full_line_input.blockSignals(True)
            self.full_line_input.setText(raw_text)
            self.full_line_input.blockSignals(False)
            return True
        elif not raw_text and not self.full_line_input.text():
            self._set_full_line_message(
                "Não foi possível detectar o texto. Digite a terceira linha manualmente.",
                error=True,
            )
        return False

    def _set_full_line_message(self, text: str, *, error: bool = False) -> None:
        self.full_line_message.setText(text)
        self.full_line_message.setProperty("error", error)
        self.full_line_message.style().unpolish(self.full_line_message)
        self.full_line_message.style().polish(self.full_line_message)

    def _set_preview_status(self, text: str, *, error: bool = False) -> None:
        self.preview_status.setText(text)
        self.preview_status.setProperty("error", error)
        self.preview_status.style().unpolish(self.preview_status)
        self.preview_status.style().polish(self.preview_status)

    @Slot()
    def start_processing(self) -> None:
        if not self.files or not self.preview_valid or self.processing:
            return
        self._cleanup_temp_dir()
        self.temp_dir = Path(tempfile.mkdtemp(prefix="sre-image-editor-"))
        self.output_files = []
        self.has_exported = False
        self.last_export_location = None
        self.failed_names.clear()
        self.processing = True
        self.pending_batch_result = None
        self.cancel_event = threading.Event()
        self.progress.set_immediate_value(0)
        self.processing_count.setText(f"0 de {len(self.files)} imagens processadas")
        self.processing_filename.setText("Preparando o lote…")
        self.cancel_button.setEnabled(True)
        self.cancel_button.setText("Cancelar edição")
        self.show_page(2)

        full_line_text = (
            self.full_line_input.text().strip() if self.full_line_toggle.isChecked() else None
        )
        worker = BatchWorker(
            list(self.files),
            self.temp_dir,
            self.sre_number.value(),
            full_line_text=full_line_text,
            cancel_event=self.cancel_event,
        )
        self.batch_worker = worker
        worker.signals.current.connect(self.processing_filename.setText)
        worker.signals.progress.connect(self.update_progress)
        worker.signals.finished.connect(self.processing_finished)
        worker.signals.cancelled.connect(self.processing_cancelled)
        self.thread_pool.start(worker)

    @Slot(int, int)
    def update_progress(self, processed: int, total: int) -> None:
        self.progress.set_animated_value(round(processed / total * 100))
        self.processing_count.setText(f"{processed} de {total} imagens processadas")

    @Slot()
    def cancel_processing(self) -> None:
        if not self.processing or self.cancel_event is None:
            return
        self.cancel_event.set()
        if self.batch_worker is None and self.pending_batch_result is not None:
            self.processing_cancelled()
            return
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Cancelando…")
        self.processing_filename.setText("Aguardando a imagem atual terminar…")

    @Slot()
    def processing_cancelled(self) -> None:
        self.progress.stop_animation()
        self.pending_batch_result = None
        self.processing = False
        self.batch_worker = None
        self._cleanup_temp_dir()
        if self.closing_after_cancel:
            self.closing_after_cancel = False
            QTimer.singleShot(0, self.close)
            return
        self.show_page(1)
        self._set_preview_status("Edição cancelada. As configurações foram preservadas.")

    @Slot(int, object)
    def processing_finished(self, completed: int, errors: object) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.processing_cancelled()
            return
        failures = list(errors)
        self.batch_worker = None
        self.pending_batch_result = (completed, failures)
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("Finalizando…")
        self.progress.set_animated_value(100)

    @Slot(int)
    def progress_target_reached(self, target: int) -> None:
        if target != 100 or self.pending_batch_result is None:
            return
        completed, failures = self.pending_batch_result
        self.pending_batch_result = None
        self._present_processing_result(completed, failures)

    def _present_processing_result(
        self, completed: int, failures: list[tuple[str, str]]
    ) -> None:
        self.processing = False
        self.show_page(3)
        self.result_export_progress.hide()
        self.result_export_status.clear()
        self.open_output_button.hide()

        if failures:
            self.failed_names = {name for name, _ in failures}
            self._cleanup_temp_dir()
            self.result_kicker.setText("LOTE INTERROMPIDO")
            self.result_title.setText("Nenhuma imagem foi exportada")
            self.result_summary.setText(
                f"{len(failures)} de {len(self.files)} imagens apresentaram problemas. "
                "Revise os arquivos abaixo antes de tentar novamente."
            )
            self.result_error_list.clear()
            for name, message in failures:
                self.result_error_list.addItem(f"{name} — {message}")
                self.result_error_list.item(self.result_error_list.count() - 1).setToolTip(message)
            self.result_error_list.show()
            self.export_actions.hide()
            self.review_button.show()
        else:
            if self.temp_dir is None:
                self.result_kicker.setText("LOTE INTERROMPIDO")
                self.result_title.setText("Os resultados temporários não foram encontrados")
                self.result_summary.setText("Volte às imagens e processe o lote novamente.")
                self.export_actions.hide()
                self.review_button.show()
                return
            self.output_files = sorted(
                (path for path in self.temp_dir.iterdir() if path.is_file()),
                key=lambda path: path.name.casefold(),
            )
            self.result_kicker.setText("LOTE CONCLUÍDO")
            self.result_title.setText(
                f"{completed} imagem pronta" if completed == 1 else f"{completed} imagens prontas"
            )
            full_line_text = (
                self.full_line_input.text().strip()
                if self.full_line_toggle.isChecked()
                else None
            )
            example_input = self.files[0].name
            example_output = edited_filename(
                example_input,
                self.sre_number.value(),
                full_line_text=full_line_text,
            )
            self.result_summary.setText(
                "Escolha como salvar. Você pode exportar para uma pasta e também "
                "criar um ZIP sem reprocessar. Os arquivos editados usam nomes "
                f"diferentes dos originais, por exemplo: {example_input} → {example_output}."
            )
            self.result_error_list.hide()
            self.export_actions.show()
            self.review_button.hide()
            self._set_export_buttons_enabled(True)

    @Slot()
    def review_failed_images(self) -> None:
        self.show_page(0)
        self.file_list.clearSelection()
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            if item.text() in self.failed_names:
                item.setSelected(True)
        self.feedback.setText("As imagens com falha estão selecionadas para revisão.")

    def _export_start_directory(self) -> Path:
        saved = self.settings.value("lastExportDirectory", "", str)
        if saved and Path(saved).is_dir():
            return Path(saved)
        return Path.home()

    @Slot()
    def choose_export_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self, "Salvar imagens em uma pasta", str(self._export_start_directory())
        )
        if selected:
            self.start_export("folder", Path(selected))

    @Slot()
    def choose_export_zip(self) -> None:
        initial = self._export_start_directory() / "imagens-editadas.zip"
        selected, _ = QFileDialog.getSaveFileName(
            self, "Salvar imagens como ZIP", str(initial), "Arquivo ZIP (*.zip)"
        )
        if selected:
            destination = Path(selected)
            if destination.suffix.lower() != ".zip":
                destination = destination.with_suffix(".zip")
            self.start_export("zip", destination)

    def start_export(self, mode: str, destination: Path) -> None:
        if self.exporting or not self.output_files:
            return
        self.exporting = True
        self._set_export_buttons_enabled(False)
        self.result_export_progress.setValue(0)
        self.result_export_progress.show()
        self._set_result_export_status(
            "Salvando imagens…" if mode == "folder" else "Criando arquivo ZIP…"
        )
        worker = ExportWorker(list(self.output_files), mode, destination)
        self.export_worker = worker
        worker.signals.progress.connect(self.update_export_progress)
        worker.signals.finished.connect(self.export_finished)
        self.thread_pool.start(worker)

    @Slot(int, int)
    def update_export_progress(self, processed: int, total: int) -> None:
        self.result_export_progress.setValue(round(processed / total * 100))

    @Slot(bool, str, str)
    def export_finished(self, succeeded: bool, location: str, error: str) -> None:
        self.exporting = False
        self.export_worker = None
        self._set_export_buttons_enabled(True)
        if succeeded:
            self.has_exported = True
            self.last_export_location = Path(location)
            self.settings.setValue("lastExportDirectory", location)
            self._set_result_export_status("Arquivos salvos com sucesso.")
            self.open_output_button.show()
        else:
            self._set_result_export_status(
                "Não foi possível salvar os arquivos. Escolha outro destino e "
                f"tente novamente. {error}",
                error=True,
            )
        self.result_export_progress.hide()

    def _set_result_export_status(self, text: str, *, error: bool = False) -> None:
        self.result_export_status.setText(text)
        self.result_export_status.setProperty("error", error)
        self.result_export_status.style().unpolish(self.result_export_status)
        self.result_export_status.style().polish(self.result_export_status)

    def _set_export_buttons_enabled(self, enabled: bool) -> None:
        self.save_folder_button.setEnabled(enabled)
        self.save_zip_button.setEnabled(enabled)
        self.new_batch_button.setEnabled(enabled)

    @Slot()
    def open_output_folder(self) -> None:
        if self.last_export_location:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_export_location)))

    @Slot()
    def start_new_batch(self) -> None:
        if self.output_files and not self.has_exported:
            answer = QMessageBox.question(
                self,
                "Descartar imagens prontas?",
                "Este lote ainda não foi salvo. Deseja descartá-lo e começar outro?",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                return
        self._cleanup_temp_dir()
        self.files.clear()
        self.file_list.clear()
        self.feedback.clear()
        self.output_files = []
        self.failed_names.clear()
        self.has_exported = False
        self.last_export_location = None
        self.preview_path = None
        self.preview_request_id += 1
        self.preview_bytes = b""
        self.preview_valid = False
        self.raw_line_loaded = False
        self.full_text_touched = False
        self.full_line_toggle.setChecked(False)
        self.full_line_input.clear()
        self.sre_number.setValue(10)
        self.before_preview.clear_source()
        self.after_preview.clear_source()
        self.open_output_button.hide()
        self.refresh_selection_state()
        self.show_page(0)

    def _cleanup_temp_dir(self) -> None:
        if self.temp_dir is not None:
            shutil.rmtree(self.temp_dir, ignore_errors=True)
        self.temp_dir = None
        self.output_files = []

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.processing:
            answer = QMessageBox.question(
                self,
                "Cancelar edição e fechar?",
                "A imagem atual terminará e os resultados temporários serão apagados.",
                QMessageBox.StandardButton.Close | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Close:
                self.closing_after_cancel = True
                self.cancel_processing()
            event.ignore()
            return
        if self.exporting:
            QMessageBox.information(
                self,
                "Exportação em andamento",
                "Aguarde os arquivos terminarem de salvar antes de fechar o aplicativo.",
            )
            event.ignore()
            return
        if self.output_files and not self.has_exported:
            answer = QMessageBox.question(
                self,
                "Fechar sem salvar?",
                "As imagens editadas deste lote ainda não foram exportadas.",
                QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if answer != QMessageBox.StandardButton.Discard:
                event.ignore()
                return
        self._cleanup_temp_dir()
        event.accept()


STYLESHEET = f"""
QWidget#root {{ background: #151515; color: #F5F3EC; }}
QWidget {{ font-size: 14px; color: #F5F3EC; }}
QStackedWidget#pages, QStackedWidget#editInputs {{ background: transparent; border: 0; }}
QLabel#eyebrow {{ color: {ACCENT}; font-size: 11px; font-weight: 700; letter-spacing: 2px; }}
QLabel#appTitle {{ font-size: 29px; font-weight: 700; }}
QLabel#pageTitle {{ font-size: 24px; font-weight: 700; }}
QLabel#resultTitle {{ font-size: 34px; font-weight: 700; }}
QLabel#processingTitle {{ font-size: 28px; font-weight: 700; }}
QLabel#processingCount {{ color: {ACCENT}; font-size: 19px; font-weight: 700; }}
QLabel#muted, QLabel#fieldMessage, QLabel#previewStatus {{ color: #AAA79F; }}
QLabel#fieldMessage[error="true"], QLabel#previewStatus[error="true"] {{ color: #E9A39A; }}
QLabel#sectionLabel {{ color: #E8E5DC; font-weight: 600; }}
QLabel#previewCaption {{ color: {ACCENT}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QFrame#dropZone {{ background: #1D1D1D; border: 1px dashed #5C5A55; border-radius: 12px; }}
QFrame#dropZone[dragActive="true"] {{ background: #28251C; border: 2px solid {ACCENT}; }}
QLabel#dropTitle {{ font-size: 18px; font-weight: 650; }}
QLabel#previewImage {{ background: transparent; border: 0; padding: 0; color: #77746C; }}
QListWidget#fileList, QListWidget#errorList, QLineEdit, QSpinBox {{
    background: #1D1D1D; border: 1px solid #393939; border-radius: 8px;
    padding: 9px; selection-background-color: #4A4020; selection-color: #FFFFFF;
}}
QListWidget#fileList:focus, QListWidget#errorList:focus, QLineEdit:focus, QSpinBox:focus {{ border: 2px solid {ACCENT}; }}
QListWidget::item {{ padding: 6px; border-radius: 4px; }}
QCheckBox#modeToggle {{ spacing: 10px; font-weight: 650; }}
QCheckBox#modeToggle::indicator {{ width: 38px; height: 20px; border-radius: 10px; background: #353535; border: 1px solid #555; }}
QCheckBox#modeToggle::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QCheckBox#modeToggle:focus {{ color: {ACCENT}; }}
QPushButton {{ min-height: 40px; padding: 0 16px; border-radius: 8px; font-weight: 600; }}
QPushButton#primaryButton {{ background: {ACCENT}; color: #17140B; border: 1px solid {ACCENT}; min-width: 120px; }}
QPushButton#primaryButton:hover {{ background: #E1C35D; }}
QPushButton#primaryButton:pressed {{ background: #B9962F; }}
QPushButton#primaryButton:disabled {{ background: #4A463A; color: #89857A; border-color: #4A463A; }}
QPushButton#secondaryButton {{ background: #242424; color: #F5F3EC; border: 1px solid #464646; }}
QPushButton#secondaryButton:hover {{ background: #303030; border-color: #686868; }}
QPushButton#textButton {{ background: transparent; color: #BBB7AD; border: 1px solid transparent; padding: 0 8px; }}
QPushButton#textButton:hover {{ color: #FFFFFF; background: #242424; }}
QPushButton:focus {{ border: 2px solid {ACCENT}; }}
QProgressBar {{ background: #252525; border: 0; border-radius: 4px; height: 8px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
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
