from qgis.PyQt import sip
from qgis.core import QgsApplication

from .provider import LandXMLTinToGeoTIFFProvider


class LandXMLTinToGeoTIFFPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

    def initGui(self):
        provider = LandXMLTinToGeoTIFFProvider()
        if QgsApplication.processingRegistry().addProvider(provider):
            self.provider = provider

    def unload(self):
        provider = self.provider
        self.provider = None
        if provider is not None and not sip.isdeleted(provider):
            QgsApplication.processingRegistry().removeProvider(provider)
