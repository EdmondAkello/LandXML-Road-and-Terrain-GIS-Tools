"""An editable table for the typed road structures (culverts, bridges, piers) of the 3D scene dialog."""

from __future__ import annotations

from .landxml.structures import TABLE_COLUMNS, TABLE_TYPES, read_schedule_text, rows_to_text, text_to_rows


def edit_structures(parent, text, roads=()):
    """Open the table with the structures in ``text``; returns the new text, or None when cancelled.

    Rows are checked with the same reader the scene uses before the table can be accepted."""
    from qgis.PyQt.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel,
                                     QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

    dialog = QDialog(parent)
    dialog.setWindowTitle("Road structures")
    dialog.resize(760, 420)
    layout = QVBoxLayout(dialog)
    layout.addWidget(QLabel(
        "One row for each culvert, bridge or pier. Station as 1+250, or 1+364-2+293 for a bridge or a run of piers. "
        "Details: box 'cells x span x rise', pipe 'lines x diameter', bridge ', span 25' or ', piers 1+500 1+640', "
        "pier 'across x along, cap side x depth'. Leave Road blank for the scene's road."))
    layout.itemAt(0).widget().setWordWrap(True)
    table = QTableWidget(0, len(TABLE_COLUMNS))
    table.setHorizontalHeaderLabels(list(TABLE_COLUMNS))
    table.horizontalHeader().setStretchLastSection(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    layout.addWidget(table, 1)

    def add_row(road="", station="", kind="box", details=""):
        row = table.rowCount()
        table.insertRow(row)
        table.setItem(row, 0, QTableWidgetItem(road))
        table.setItem(row, 1, QTableWidgetItem(station))
        combo = QComboBox()
        combo.addItems(list(TABLE_TYPES))
        combo.setCurrentText(kind if kind in TABLE_TYPES else "box")
        table.setCellWidget(row, 2, combo)
        table.setItem(row, 3, QTableWidgetItem(details))

    def rows():
        found = []
        for r in range(table.rowCount()):
            cell = lambda c: (table.item(r, c).text() if table.item(r, c) else "")      # noqa: E731
            found.append((cell(0), cell(1), table.cellWidget(r, 2).currentText(), cell(3)))
        return found

    try:
        for row in text_to_rows(text):
            add_row(*row)
    except ValueError as error:
        layout.insertWidget(1, QLabel(f"The typed text could not be read as a table ({error}); the table starts empty."))
    tools = QHBoxLayout()
    for label, action in (("Add row", lambda: add_row()),
                          ("Remove selected", lambda: [table.removeRow(i) for i in sorted({x.row() for x in table.selectedIndexes()}, reverse=True)])):
        button = QPushButton(label)
        button.clicked.connect(lambda _checked=False, a=action: a())
        tools.addWidget(button)
    tools.addStretch(1)
    layout.addLayout(tools)
    message = QLabel()
    message.setStyleSheet("color:#b00020;")
    layout.addWidget(message)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
    layout.addWidget(buttons)
    result = {}

    def accept():
        try:
            new = rows_to_text(rows())
            read_schedule_text(new)                       # the scene's own reader has the last word
        except ValueError as error:
            message.setText(str(error))
            return
        result["text"] = new
        dialog.accept()

    buttons.accepted.connect(accept)
    buttons.rejected.connect(dialog.reject)
    return result.get("text") if dialog.exec() and "text" in result else None
