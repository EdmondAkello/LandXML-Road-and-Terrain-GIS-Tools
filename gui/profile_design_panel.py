"""The Design tab of the profile viewer: design a vertical profile on the long section and save it to LandXML.

The chart does the dragging (``ProfileChart.editor``); this panel holds the design, its PVI table, the checks against a
design standard and saving. Everything is in the file's own station and level units.
"""

from __future__ import annotations

import os

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..landxml.criteria import list_standards, load_criteria
from ..landxml.profile import read_profile_controls
from ..landxml.profile_design import ProfileDesign, splice_profile

SEVERITY_COLOURS = {"high": "#b3261e", "medium": "#b26a00", "low": "#5b6575"}
COLUMNS = ["Station", "Level", "Curve", "K", "Grade after (%)"]


class ProfileDesignPanel(QWidget):
    changed = pyqtSignal()                 # the design changed (the dock redraws)
    editingToggled = pyqtSignal(bool)
    stationRequested = pyqtSignal(float)
    saved = pyqtSignal(str, str, str)      # path, alignment, profile name

    def __init__(self, parent=None):
        super().__init__(parent)
        self.design = None
        self.document = None
        self.path = None
        self.alignment = None
        self.ground = None
        self.designs = []
        self.station_range = None
        self._filling = False
        self._build()

    # ---------------------------------------------------------- layout
    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        start = QHBoxLayout()
        self.new_button = QPushButton("New from ground")
        self.new_button.setToolTip("Start a design on the existing ground: PVIs at both ends, and every 'PVIs every' "
                                   "(averaged over that length) when it is set.")
        self.spacing_spin = QDoubleSpinBox()
        self.spacing_spin.setRange(0.0, 100000.0)
        self.spacing_spin.setDecimals(0)
        self.spacing_spin.setSingleStep(50.0)
        self.spacing_spin.setValue(200.0)
        self.spacing_spin.setSpecialValueText("ends only")
        self.spacing_spin.setPrefix("PVIs every ")
        self.copy_combo = QComboBox()
        self.copy_combo.setToolTip("An existing design profile to start from.")
        self.copy_button = QPushButton("Copy")
        for widget in (self.new_button, self.spacing_spin, QLabel(" or "), self.copy_combo, self.copy_button):
            start.addWidget(widget)
        start.addStretch(1)
        self.help_button = QToolButton()
        self.help_button.setText("Help")
        self.help_button.setToolTip("Open the user guide at 'Designing a profile' (works offline)")
        self.help_button.clicked.connect(self._help)
        start.addWidget(self.help_button)
        layout.addLayout(start)

        edit = QHBoxLayout()
        self.name_edit = QLineEdit("Design FRL")
        self.name_edit.setToolTip("Name of the profile in the LandXML (a profile of the same name is replaced).")
        self.edit_button = QToolButton()
        self.edit_button.setText("Edit on chart")
        self.edit_button.setCheckable(True)
        self.edit_button.setToolTip("Drag a PVI to move it (Shift: up and down only), double-click the chart to add one, "
                                    "right-click a PVI for its curve, or select it and press Delete.")
        self.undo_button = QToolButton()
        self.undo_button.setText("Undo")
        self.redo_button = QToolButton()
        self.redo_button.setText("Redo")
        self.save_button = QPushButton("Save to LandXML…")
        self.save_button.setToolTip("Write the design into a copy of the LandXML file (the source file is never changed).")
        for widget in (QLabel("Name"), self.name_edit, self.edit_button, self.undo_button, self.redo_button, self.save_button):
            edit.addWidget(widget)
        layout.addLayout(edit)

        check = QGridLayout()
        self.standard_combo = QComboBox()
        self.standard_combo.addItem("(no standard)", None)
        for identifier, name, _path in list_standards():
            self.standard_combo.addItem(name, identifier)
        self.speed_spin = QDoubleSpinBox()
        self.speed_spin.setRange(0.0, 200.0)
        self.speed_spin.setDecimals(0)
        self.speed_spin.setValue(80.0)
        self.speed_spin.setSpecialValueText("no speed")
        self.grade_spin = QDoubleSpinBox()
        self.grade_spin.setRange(0.0, 30.0)
        self.grade_spin.setDecimals(1)
        self.grade_spin.setValue(0.0)
        self.grade_spin.setSuffix(" %")
        self.grade_spin.setSpecialValueText("no limit")
        check.addWidget(QLabel("Standard"), 0, 0)
        check.addWidget(self.standard_combo, 0, 1)
        check.addWidget(QLabel("Design speed"), 0, 2)
        check.addWidget(self.speed_spin, 0, 3)
        check.addWidget(QLabel("Max grade"), 0, 4)
        check.addWidget(self.grade_spin, 0, 5)
        check.setColumnStretch(1, 1)
        layout.addLayout(check)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setToolTip("Type a station, level, curve length or K to set it exactly.")
        layout.addWidget(self.table, 2)
        self.checks_list = QListWidget()
        self.checks_list.setToolTip("Click a finding to show it on the chart.")
        layout.addWidget(self.checks_list, 1)
        self.status = QLabel("Choose an alignment with a ground profile, then start a design.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self.new_button.clicked.connect(self.new_from_ground)
        self.copy_button.clicked.connect(self.copy_design)
        self.edit_button.toggled.connect(self._toggle_editing)
        self.undo_button.clicked.connect(lambda: self._history(self.design.undo))
        self.redo_button.clicked.connect(lambda: self._history(self.design.redo))
        self.save_button.clicked.connect(self.save)
        self.name_edit.editingFinished.connect(self._rename)
        for widget in (self.standard_combo,):
            widget.currentIndexChanged.connect(self.sync)
        for widget in (self.speed_spin, self.grade_spin):
            widget.valueChanged.connect(self.sync)
        self.table.itemChanged.connect(self._cell_edited)
        self.table.itemSelectionChanged.connect(self._row_selected)
        self.checks_list.itemClicked.connect(lambda item: self.stationRequested.emit(float(item.data(Qt.ItemDataRole.UserRole))))
        self._enable()

    def _help(self):
        from ..project_settings import open_guide

        open_guide("profile-design")

    # ---------------------------------------------------------- context
    def set_context(self, document, path, alignment, profiles, ground, station_range):
        """The alignment being viewed: its profiles, the chosen ground profile and its station range."""
        changed = alignment != self.alignment or path != self.path
        self.document, self.path, self.alignment, self.ground = document, path, alignment, ground
        self.station_range = station_range
        self.designs = [p for p in profiles if p.kind == "design" and p.element is not None]
        self.copy_combo.clear()
        for profile in self.designs:
            self.copy_combo.addItem(profile.name)
        if changed:
            self.design = None
            self.edit_button.setChecked(False)
        self.sync()

    @property
    def editing(self):
        return self.design is not None and self.edit_button.isChecked()

    # ---------------------------------------------------------- starting
    def new_from_ground(self):
        if self.ground is None or self.station_range is None:
            self.status.setText("Choose an existing-ground profile first (Existing, above).")
            return
        points = [p for part in self.ground.parts for p in part]
        start, end = self.station_range
        try:
            self.design = ProfileDesign.from_ground(points, start, end, self.spacing_spin.value(), self.name_edit.text() or "Design FRL")
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._started()

    def copy_design(self):
        index = self.copy_combo.currentIndex()
        if index < 0 or index >= len(self.designs):
            return
        try:
            controls = read_profile_controls(self.designs[index].element)
            self.design = ProfileDesign.from_controls(controls, self.name_edit.text() or "Design FRL")
        except ValueError as error:
            self.status.setText(f"That profile cannot be edited: {error}")
            return
        self._started()

    def _started(self):
        self.edit_button.setChecked(True)
        self.sync()
        self.changed.emit()

    def _toggle_editing(self, on):
        self.editingToggled.emit(bool(on) and self.design is not None)
        self.changed.emit()

    # ---------------------------------------------------------- editing
    def _history(self, action):
        if self.design is not None and action():
            self.sync()
            self.changed.emit()

    def _rename(self):
        if self.design is not None and self.name_edit.text().strip():
            self.design.name = self.name_edit.text().strip()

    def _cell_edited(self, item):
        if self._filling or self.design is None:
            return
        row, column = item.row(), item.column()
        try:
            value = float(item.text().replace(",", "."))
            if column == 0:
                self.design.move(row, station=value)
            elif column == 1:
                self.design.move(row, level=value)
            elif column == 2:
                self.design.set_curve(row, value)
            elif column == 3:
                self.design.set_k(row, value)
        except ValueError as error:
            self.status.setText(str(error) if str(error) and not str(error).startswith("could not convert") else "Type a number.")
        self.sync()
        self.changed.emit()

    def _row_selected(self):
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) == 1 and self.design is not None:
            row = rows.pop()
            self.stationRequested.emit(self.design.pvis[row][0])

    def select_row(self, index):
        self.table.blockSignals(True)
        self.table.clearSelection()
        if index is not None and 0 <= index < self.table.rowCount():
            self.table.selectRow(index)
        self.table.blockSignals(False)

    def show_menu(self, index, position):
        """The right-click menu of a PVI on the chart."""
        if self.design is None:
            return
        menu = QMenu(self)
        inner = 0 < index < len(self.design.pvis) - 1
        curve = menu.addAction("Curve length…")
        k_value = menu.addAction("K value…")
        remove = menu.addAction("Remove curve")
        menu.addSeparator()
        delete = menu.addAction("Delete PVI")
        for action in (curve, k_value, remove, delete):
            action.setEnabled(inner)
        chosen = menu.exec(position)
        self.apply_menu(index, {curve: "curve", k_value: "k", remove: "remove", delete: "delete"}.get(chosen))

    def apply_menu(self, index, choice, value=None):
        if self.design is None or choice is None:
            return
        try:
            if choice == "delete":
                self.design.delete(index)
            elif choice == "remove":
                self.design.set_curve(index, 0.0)
            elif choice in ("curve", "k"):
                if value is None:
                    current = self.design.pvis[index][2] if choice == "curve" else \
                        next((c["k"] or 0.0 for c in self.design.curves() if c["index"] == index), 0.0)
                    value, ok = QInputDialog.getDouble(self, "Vertical curve", "Curve length" if choice == "curve" else "K value",
                                                       current, 0.0, 100000.0, 2)
                    if not ok:
                        return
                (self.design.set_curve if choice == "curve" else self.design.set_k)(index, value)
        except ValueError as error:
            self.status.setText(str(error))
        self.sync()
        self.changed.emit()

    # ---------------------------------------------------------- showing
    def criteria(self):
        identifier = self.standard_combo.currentData()
        if not identifier:
            return None, ""
        try:
            criteria = load_criteria(identifier)
            if self.document is not None:
                criteria.set_linear_unit(self.document.horizontal_unit, self.document.vertical_unit)
            return criteria, ""
        except ValueError as error:
            return None, f"Standard not applied: {error}"

    def sync(self):
        """Refill the table and the checks from the design."""
        self._enable()
        self._filling = True
        self.table.setRowCount(0)
        self.checks_list.clear()
        if self.design is None:
            self._filling = False
            return
        curves = {c["index"]: c for c in self.design.curves()}
        pvis = self.design.pvis
        self.table.setRowCount(len(pvis))
        for row, (s, z, length) in enumerate(pvis):
            values = [f"{s:.2f}", f"{z:.3f}", f"{length:.1f}" if length else "",
                      f"{curves[row]['k']:.1f}" if row in curves and curves[row]["k"] else "",
                      f"{self.design.grade(row) * 100:+.3f}" if row < len(pvis) - 1 else ""]
            for column, text in enumerate(values):
                item = QTableWidgetItem(text)
                end = row in (0, len(pvis) - 1)
                editable = column == 1 or (column in (0, 2, 3) and not end)
                if not editable:
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.table.setItem(row, column, item)
        self._filling = False
        criteria, note = self.criteria()
        speed = self.speed_spin.value() or None
        findings = self.design.checks(criteria, speed, self.grade_spin.value() or None)
        for severity, station, text in findings:
            item = QListWidgetItem(f"{station:.1f}: {text}")
            item.setForeground(QColor(SEVERITY_COLOURS.get(severity, "#1d2430")))
            item.setData(Qt.ItemDataRole.UserRole, station)
            self.checks_list.addItem(item)
        self.status.setText(note or (f"{len(pvis)} PVIs, {len(findings)} finding{'s' if len(findings) != 1 else ''}"
                                     + (f" against {criteria.name} at {speed:g} {criteria.speed_unit}." if criteria and speed else ".")))

    def _enable(self):
        has = self.design is not None
        for widget in (self.edit_button, self.save_button, self.name_edit):
            widget.setEnabled(has)
        self.undo_button.setEnabled(has and self.design.can_undo)
        self.redo_button.setEnabled(has and self.design.can_redo)
        self.new_button.setEnabled(self.ground is not None)
        self.copy_button.setEnabled(bool(self.designs))

    # ---------------------------------------------------------- saving
    def save(self, path=None):
        """Write the design into a copy of the LandXML; returns the path written, or None."""
        if self.design is None or not self.path:
            return None
        self._rename()
        if path is None:
            base, _ext = os.path.splitext(self.path)
            path, _filter = QFileDialog.getSaveFileName(self, "Save the design to a copy of the LandXML",
                                                        f"{base}_design.xml", "LandXML (*.xml)")
            if not path:
                return None
        if os.path.abspath(path) == os.path.abspath(self.path):
            self.status.setText("Choose a new file name: the source LandXML is never changed.")
            return None
        try:
            with open(self.path, encoding="utf-8-sig") as stream:
                text = stream.read()
            text = splice_profile(text, self.alignment, self.design.profile())
            with open(path, "w", encoding="utf-8") as stream:
                stream.write(text)
        except (OSError, ValueError, UnicodeDecodeError) as error:
            self.status.setText(f"Not saved: {error}")
            return None
        self.status.setText(f"Saved '{self.design.name}' on '{self.alignment}' to {path}.")
        self.saved.emit(path, self.alignment, self.design.name)
        return path
