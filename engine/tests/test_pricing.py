import unittest
from decimal import Decimal

from nutrivalue.pricing import protein_value


class ProteinValueTests(unittest.TestCase):
    def test_hypothetical_example(self):
        result = protein_value("60", "400", "18")
        self.assertEqual(result.total_protein_g, Decimal("72"))
        self.assertEqual(result.nok_per_g_protein.quantize(Decimal("0.01")), Decimal("0.83"))

    def test_invalid_denominator_and_inputs(self):
        for price, mass, protein in [
            (0, 400, 18), (-1, 400, 18), (60, 0, 18), (60, -1, 18),
            (60, 400, 0), (60, 400, -1), (60, 400, 101),
            (60, 400, "NaN"), (60, "Infinity", 18),
        ]:
            with self.subTest(price=price, mass=mass, protein=protein):
                with self.assertRaises(ValueError):
                    protein_value(price, mass, protein)


if __name__ == "__main__":
    unittest.main()
