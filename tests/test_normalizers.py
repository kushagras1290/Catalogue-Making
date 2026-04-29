import unittest
from decimal import Decimal

from catalog_automation.normalizers import derive_weights, slugify, norm_key

class TestNormalizers(unittest.TestCase):
    def test_ratti_to_carat(self):
        carat, ratti = derive_weights("", "3")
        self.assertEqual(carat, Decimal("2.73"))
        self.assertEqual(ratti, Decimal("3.00"))

    def test_carat_to_ratti(self):
        carat, ratti = derive_weights("2", "")
        self.assertEqual(carat, Decimal("2.00"))
        self.assertEqual(ratti, Decimal("2.20"))

    def test_slugify(self):
        self.assertEqual(slugify("Royal Blue Sapphire / Neelam"), "royal-blue-sapphire-neelam")

    def test_norm_key(self):
        self.assertEqual(norm_key("  Royal   Blue "), "royal blue")

if __name__ == "__main__":
    unittest.main()
