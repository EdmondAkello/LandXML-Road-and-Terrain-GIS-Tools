"""Small Qt/QGIS compatibility surface shared by QGIS 3.44 and 4.2."""

from qgis.PyQt.QtCore import QMetaType

FIELD_STRING = QMetaType.Type.QString
FIELD_DOUBLE = QMetaType.Type.Double
FIELD_INT = QMetaType.Type.Int


def panel_wrappers(panel):
    """The parameter widget wrappers of a Processing dialog panel by name: an attribute (dict) up to QGIS 4.2, a method returning a list in 4.4."""
    wrappers = panel.wrappers
    wrappers = wrappers() if callable(wrappers) else wrappers
    if isinstance(wrappers, dict):
        return wrappers
    return {wrapper.parameterDefinition().name(): wrapper for wrapper in wrappers}      # QGIS 4.4: a list


def mark_advanced_definition(definition):
    from qgis.core import Qgis

    definition.setFlags(definition.flags() | Qgis.ProcessingParameterFlag.Advanced)


def mark_advanced(algorithm, names):
    """Move the named parameters into the dialog's Advanced section (QGIS 3.44 and 4.x)."""
    from qgis.core import Qgis

    for name in names:
        definition = algorithm.parameterDefinition(name)
        if definition is not None:
            definition.setFlags(definition.flags() | Qgis.ProcessingParameterFlag.Advanced)
