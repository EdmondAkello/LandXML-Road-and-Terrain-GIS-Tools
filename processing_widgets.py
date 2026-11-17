"""Processing parameters whose dropdown lists names from the chosen LandXML file.

The parameter stays a plain string, so scripts, models and batch runs keep
working; in the dialog it is an editable dropdown refreshed whenever the
LandXML file parameter changes. Leaving it blank means "detect".
"""

from __future__ import annotations

import os

from qgis.core import QgsProcessingParameterString

from .landxml.catalog import read_catalog

try:  # The Processing plugin's GUI wrappers are absent in some headless runs.
    from processing.gui.wrappers import WidgetWrapper
except ImportError:  # pragma: no cover - depends on the runtime
    WidgetWrapper = None


def name_param(name, description, kind, default="", optional=True, file_param="INPUT", fallback_file_param=None):
    parameter = QgsProcessingParameterString(name, description, defaultValue=default, optional=optional)
    if WidgetWrapper is not None:
        parameter.setMetadata(
            {"widget_wrapper": {"class": LandXMLNameWidgetWrapper, "kind": kind, "file_param": file_param,
                                "fallback_file_param": fallback_file_param}}
        )
    return parameter


def names_param(name, description, kind, separator=";", file_param="INPUT", layers_param=None):
    """Several names from the LandXML file (and the layers chosen in ``layers_param``), joined by
    ``separator``: a text field with a picker."""
    parameter = QgsProcessingParameterString(name, description, defaultValue="", optional=True)
    if WidgetWrapper is not None:
        parameter.setMetadata({"widget_wrapper": {"class": LandXMLNamesWidgetWrapper, "kind": kind,
                                                  "file_param": file_param, "separator": separator,
                                                  "layers_param": layers_param}})
    return parameter


def surfaces_param(name, description, file_param="INPUT"):
    """'EG = plain #cfd8c4; FG = depth over EG': a text field with a table editor (surface, style, drawn over)."""
    parameter = QgsProcessingParameterString(name, description, defaultValue="", optional=True)
    if WidgetWrapper is not None:
        parameter.setMetadata({"widget_wrapper": {"class": SurfaceStylesWidgetWrapper, "file_param": file_param}})
    return parameter


def style_param(name, description, default="", optional=False, attribute=None):
    """A surface style: an editable list of built-in styles and the user's saved ones."""
    parameter = QgsProcessingParameterString(name, description, defaultValue=default, optional=optional)
    if WidgetWrapper is not None:
        parameter.setMetadata({"widget_wrapper": {"class": StyleWidgetWrapper, "attribute": attribute}})
    return parameter


BUILT_IN_STYLES = [
    ("Elevation (9 equal classes, terrain colours)", "elevation"),
    ("Elevation, 12 equal classes", "elevation: equal 12"),
    ("Elevation, 6 quantile classes", "elevation: quantile 6 | terrain"),
    ("Elevation in greys", "elevation: equal 6 | greys"),
    ("Slope classes (flat, sloping, batter, near-vertical)", "slope"),
    ("Slope: 1, 5, 30 %", "slope: 1, 5, 30"),
    ("Depth: cut and fill (needs 'over' an existing surface)", "depth"),
    ("Aspect (8 directions)", "aspect"),
    ("Plain grey-green", "plain #cfd8c4"),
    ("Plain sand", "plain #e3d3b0"),
    ("Finished road: 7 m, 1.5 m shoulders, 2 lanes", "road: 7, 1.5, 2"),
    ("Finished road: 11 m, 2.5 m shoulders, 4 lanes", "road: 11, 2.5, 4"),
]
STYLE_SETTINGS_KEY = "LandXMLTools/userStyles"


def user_styles():
    """{name: style text} saved by the user in QGIS settings."""
    import json

    from qgis.core import QgsSettings

    try:
        saved = json.loads(QgsSettings().value(STYLE_SETTINGS_KEY, "{}") or "{}")
    except (TypeError, ValueError):
        return {}
    return {str(k): str(v) for k, v in saved.items()} if isinstance(saved, dict) else {}


def save_user_style(name, text):
    """Check the style text and keep it under ``name`` (empty ``text`` deletes it)."""
    import json

    from qgis.core import QgsSettings

    from .landxml.surface_style import parse_style

    styles = user_styles()
    if text:
        parse_style(text)                  # raises ValueError for a style the tools cannot read
        styles[name] = text
    else:
        styles.pop(name, None)
    QgsSettings().setValue(STYLE_SETTINGS_KEY, json.dumps(styles))
    return styles


def style_choices():
    """[(label, text)]: built-in styles, then the user's saved ones."""
    return BUILT_IN_STYLES + [(f"★ {name}", text) for name, text in sorted(user_styles().items())]


def names_for(path, kind):
    if not path or not os.path.isfile(path):
        return []
    try:
        return read_catalog(path).names(kind)
    except (OSError, ValueError):
        return []


if WidgetWrapper is not None:
    from qgis.PyQt.QtWidgets import QComboBox

    class LandXMLNameWidgetWrapper(WidgetWrapper):
        def createWidget(self, kind="surfaces", file_param="INPUT", fallback_file_param=None, **_kwargs):
            self._kind = kind
            self._file_param = file_param
            self._fallback_file_param = fallback_file_param
            combo = QComboBox()
            combo.setEditable(True)
            combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            combo.lineEdit().setPlaceholderText("(blank = detect automatically)")
            combo.currentTextChanged.connect(lambda _text: self.widgetValueHasChanged.emit(self))
            return combo

        def postInitialize(self, wrappers):
            self._sources = {wrapper.parameterDefinition().name(): wrapper for wrapper in wrappers
                             if wrapper.parameterDefinition().name() in (self._file_param, self._fallback_file_param)}
            for wrapper in self._sources.values():
                wrapper.widgetValueHasChanged.connect(lambda changed: self.refresh(changed))
            self.refresh(self._sources.get(self._file_param))

        def refresh(self, wrapper=None):
            path = ""
            for key in (self._file_param, self._fallback_file_param):
                source = getattr(self, "_sources", {}).get(key) or wrapper
                if source is None:
                    continue
                try:
                    value = source.parameterValue()
                except AttributeError:
                    value = source.value()
                if isinstance(value, str) and value:
                    path = value
                    break
            combo = self.widget
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("")
            combo.addItems(names_for(path, self._kind))
            combo.setEditText(current)
            combo.blockSignals(False)

        def setValue(self, value):
            self.widget.setEditText("" if value is None else str(value))

        def value(self):
            return self.widget.currentText().strip()
    from qgis.PyQt.QtWidgets import (
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QHeaderView,
        QInputDialog,
        QLabel,
        QLineEdit,
        QListWidget,
        QListWidgetItem,
        QMessageBox,
        QPushButton,
        QTableWidget,
        QToolButton,
        QVBoxLayout,
        QWidget,
    )
    from qgis.PyQt.QtCore import Qt

    def _file_from(wrappers, name):
        for wrapper in wrappers.values():
            try:
                value = wrapper.parameterValue()
            except AttributeError:
                value = wrapper.value()
            if isinstance(value, str) and value:
                return value
        return ""

    class _FileAware(WidgetWrapper):
        """Keeps track of the LandXML file parameter so pickers list its names."""

        def postInitialize(self, wrappers):
            self._sources = {w.parameterDefinition().name(): w for w in wrappers
                             if w.parameterDefinition().name() == getattr(self, "_file_param", "INPUT")}
            self._layers = next((w for w in wrappers
                                 if w.parameterDefinition().name() == getattr(self, "_layers_param", None)), None)

        def layer_names(self):
            """Names of the layers chosen in the linked layer parameter (rasters for the ground)."""
            if getattr(self, "_layers", None) is None:
                return []
            from qgis.core import QgsProject

            try:
                value = self._layers.parameterValue()
            except AttributeError:
                value = self._layers.value()
            out = []
            for item in value or []:
                layer = item if hasattr(item, "name") else QgsProject.instance().mapLayer(str(item))
                if layer is not None:
                    out.append(layer.name())
                elif isinstance(item, str):
                    out.append(os.path.splitext(os.path.basename(item))[0])
            return out

        def landxml_path(self):
            return _file_from(getattr(self, "_sources", {}), getattr(self, "_file_param", "INPUT"))

        def setValue(self, value):
            self._edit.setText("" if value is None else str(value))

        def value(self):
            return self._edit.text().strip()

        def _line_with_button(self, button_text, tooltip, handler):
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            self._edit = QLineEdit()
            self._edit.textChanged.connect(lambda _t: self.widgetValueHasChanged.emit(self))
            button = QToolButton()
            button.setText(button_text)
            button.setToolTip(tooltip)
            button.clicked.connect(handler)
            row.addWidget(self._edit)
            row.addWidget(button)
            return box

    class LandXMLNamesWidgetWrapper(_FileAware):
        """A text field (names joined by the separator) with a checklist of the file's names."""

        def createWidget(self, kind="alignments", file_param="INPUT", separator=";", layers_param=None, **_kwargs):
            self._kind, self._file_param, self._separator = kind, file_param, separator
            self._layers_param = layers_param
            widget = self._line_with_button("…", "Choose from the LandXML file", self.choose)
            self._edit.setPlaceholderText(f"(blank = none; several names separated by '{separator.strip()}')")
            return widget

        def choose(self):
            names = names_for(self.landxml_path(), self._kind) + self.layer_names()
            chosen = [t.strip() for t in self._edit.text().split(self._separator.strip()) if t.strip()]
            dialog = QDialog(self._edit)
            dialog.setWindowTitle("Choose names")
            layout = QVBoxLayout(dialog)
            if not names:
                layout.addWidget(QLabel("Choose the LandXML file first (no names found)."))
            box = QListWidget()
            for name in names + [n for n in chosen if n not in names]:
                item = QListWidgetItem(name)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if name in chosen else Qt.CheckState.Unchecked)
                box.addItem(item)
            layout.addWidget(box)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec():
                picked = [box.item(i).text() for i in range(box.count())
                          if box.item(i).checkState() == Qt.CheckState.Checked]
                self._edit.setText(f"{self._separator.strip()} ".join(picked))

    def _style_combo(text=""):
        from qgis.PyQt.QtWidgets import QComboBox

        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for label, value in style_choices():
            combo.addItem(f"{value}    — {label}", value)
        combo.setEditText(text)
        # choosing an item puts the style text itself in the field
        combo.activated.connect(lambda index: combo.setEditText(combo.itemData(index) or ""))
        return combo

    class StyleWidgetWrapper(WidgetWrapper):
        """An editable list of built-in and saved styles, with buttons to save or remove the user's own."""

        def createWidget(self, attribute=None, **_kwargs):
            box = QWidget()
            row = QHBoxLayout(box)
            row.setContentsMargins(0, 0, 0, 0)
            self._combo = _style_combo()
            self._combo.currentTextChanged.connect(lambda _t: self.widgetValueHasChanged.emit(self))
            save = QToolButton()
            save.setText("★")
            save.setToolTip("Save this style under a name for later (kept in your QGIS settings)")
            save.clicked.connect(self.save)
            remove = QToolButton()
            remove.setText("−")
            remove.setToolTip("Remove one of your saved styles")
            remove.clicked.connect(self.remove)
            row.addWidget(self._combo, 1)
            row.addWidget(save)
            row.addWidget(remove)
            return box

        def _reload(self):
            text = self._combo.currentText()
            replacement = _style_combo(text)
            self._combo.clear()
            for i in range(replacement.count()):
                self._combo.addItem(replacement.itemText(i), replacement.itemData(i))
            self._combo.setEditText(text)

        def save(self):
            text = self._combo.currentText().strip()
            name, ok = QInputDialog.getText(self._combo, "Save style", "Name for this style:")
            if not ok or not name.strip():
                return
            try:
                save_user_style(name.strip(), text)
            except (ValueError, KeyError) as exc:
                QMessageBox.warning(self._combo, "Save style", f"This style cannot be read: {exc}")
                return
            self._reload()

        def remove(self):
            names = sorted(user_styles())
            if not names:
                QMessageBox.information(self._combo, "Saved styles", "You have no saved styles yet.")
                return
            name, ok = QInputDialog.getItem(self._combo, "Remove a saved style", "Style:", names, 0, False)
            if ok and name:
                save_user_style(name, "")
                self._reload()

        def setValue(self, value):
            self._combo.setEditText("" if value is None else str(value))

        def value(self):
            return self._combo.currentText().strip()

    class SurfaceStylesWidgetWrapper(_FileAware):
        """'Name = style over EG; ...' with a table editor listing the file's surfaces and the styles."""

        def createWidget(self, file_param="INPUT", **_kwargs):
            self._file_param = file_param
            widget = self._line_with_button("Edit…", "Choose surfaces, their styles and which surface each is drawn over",
                                            self.edit)
            self._edit.setPlaceholderText("e.g. EG = plain #cfd8c4; FG = depth over EG")
            return widget

        def edit(self):
            from qgis.PyQt.QtWidgets import QComboBox

            from .scene_algorithm import parse_surface_list

            names = names_for(self.landxml_path(), "surfaces")
            rows = parse_surface_list(self._edit.text())
            dialog = QDialog(self._edit)
            dialog.setWindowTitle("Surfaces and styles")
            dialog.resize(760, 360)
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("A surface drawn over another (for example a design over existing ground) "
                                    "cuts that ground away under it; 'depth' styles need it."))
            table = QTableWidget(0, 3)
            table.setHorizontalHeaderLabels(["Surface", "Style", "Drawn over"])
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

            def add_row(name="", style="", over=""):
                r = table.rowCount()
                table.insertRow(r)
                surface = QComboBox()
                surface.setEditable(True)
                surface.addItems(names)
                surface.setEditText(name)
                base = QComboBox()
                base.setEditable(True)
                base.addItems([""] + names)
                base.setEditText(over)
                table.setCellWidget(r, 0, surface)
                table.setCellWidget(r, 1, _style_combo(style))
                table.setCellWidget(r, 2, base)

            for name, style, over in rows or [("", "", "")]:
                add_row(name, style, over)
            layout.addWidget(table)
            buttons_row = QHBoxLayout()
            add = QPushButton("Add surface")
            add.clicked.connect(lambda: add_row())
            drop = QPushButton("Remove selected")
            drop.clicked.connect(lambda: table.removeRow(table.currentRow()) if table.currentRow() >= 0 else None)
            buttons_row.addWidget(add)
            buttons_row.addWidget(drop)
            buttons_row.addStretch(1)
            layout.addLayout(buttons_row)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec():
                parts = []
                for r in range(table.rowCount()):
                    name = table.cellWidget(r, 0).currentText().strip()
                    style = table.cellWidget(r, 1).currentText().strip()
                    over = table.cellWidget(r, 2).currentText().strip()
                    if not name:
                        continue
                    text = name + (f" = {style}" if style else "") + (f" over {over}" if over else "")
                    if over and not style:
                        text = f"{name} = depth over {over}"
                    parts.append(text)
                self._edit.setText("; ".join(parts))
else:  # pragma: no cover
    LandXMLNameWidgetWrapper = LandXMLNamesWidgetWrapper = SurfaceStylesWidgetWrapper = StyleWidgetWrapper = None
