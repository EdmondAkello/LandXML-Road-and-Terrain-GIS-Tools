"""Small Qt/QGIS compatibility surface shared by QGIS 3.44 and 4.2."""

from qgis.PyQt.QtCore import QMetaType

FIELD_STRING = QMetaType.Type.QString
FIELD_DOUBLE = QMetaType.Type.Double
FIELD_INT = QMetaType.Type.Int


def mark_advanced_definition(definition):
    from qgis.core import Qgis, QgsProcessingParameterDefinition

    flag = getattr(getattr(Qgis, "ProcessingParameterFlag", None), "Advanced", None)
    if flag is None:
        flag = QgsProcessingParameterDefinition.FlagAdvanced
    definition.setFlags(definition.flags() | flag)


def mark_advanced(algorithm, names):
    """Move the named parameters into the dialog's Advanced section (QGIS 3.44 and 4.x)."""
    from qgis.core import Qgis, QgsProcessingParameterDefinition

    flag = getattr(getattr(Qgis, "ProcessingParameterFlag", None), "Advanced", None)
    if flag is None:
        flag = QgsProcessingParameterDefinition.FlagAdvanced
    for name in names:
        definition = algorithm.parameterDefinition(name)
        if definition is not None:
            definition.setFlags(definition.flags() | flag)
