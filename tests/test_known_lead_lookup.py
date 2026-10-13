import csv
import io
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from lookup_known_leads import lookup


class KnownLeadLookupTests(unittest.TestCase):
    def index(self, rows):
        stream = io.StringIO()
        writer = csv.writer(stream, delimiter='\t')
        writer.writerow(['name', 'website', 'phone', 'advertisedAddress'])
        writer.writerows(rows)
        return stream.getvalue()

    def test_each_query_is_scanned_beyond_other_queries_many_matches(self):
        rows = [('On Track Tutoring ' + str(i), '', '', '') for i in range(20)]
        rows.append(('Heather Moore, Dyslexia Tutoring', '', '07913 820845', ''))
        result = lookup(self.index(rows), ['On Track Tutoring', 'Heather Moore', 'Absent Tutor'])
        self.assertEqual([r['totalMatches'] for r in result], [20, 1, 0])
        self.assertEqual(len(result[0]['matches']), 2)
        self.assertTrue(all(r['complete'] for r in result))
        self.assertTrue(result[0]['matchesTruncated'])
        self.assertEqual(result[1]['matches'][0]['name'], rows[-1][0])

    def test_hyphens_accents_and_case_do_not_hide_an_existing_name(self):
        text = self.index([('Taylor Made Education', '', '', ''), ('Mrs Siân Jacklin', '', '', '')])
        self.assertEqual([r['totalMatches'] for r in lookup(text, ['taylor-made education', 'Sian Jacklin'])], [1, 1])

    def test_quoted_tsv_fields_and_multiline_addresses_preserve_record_boundaries(self):
        text = self.index([('Jane\tSmith', 'https://jane.example/', '', 'Aylesbury\nBuckinghamshire'),
                           ('Alex Jones', '', '', 'London')])
        result = lookup(text, ['Jane Smith', 'Alex Jones'])
        self.assertEqual([r['totalMatches'] for r in result], [1, 1])
        self.assertIn('Buckinghamshire', result[0]['matches'][0]['advertisedAddress'])

    def test_uk_phone_formats_and_multiple_advertised_numbers_match(self):
        text = self.index([('Tracy Stewart', '', '01296 747442 / 07958 627536', ''),
                           ('Heather Moore', '', '+447913820845', '')])
        result = lookup(text, ['07958627536', '07913 820845'])
        self.assertEqual([r['totalMatches'] for r in result], [1, 1])

    def test_domains_match_as_tokens_and_queries_are_literal_data(self):
        text = self.index([('Teacher', 'https://www.theenglishteacher.uk/', '', ''),
                           ('Other', 'https://nottheenglishteacher.uk/', '', '')])
        self.assertEqual(lookup(text, ['theenglishteacher.uk'])[0]['totalMatches'], 1)
        self.assertEqual(lookup(text, ['.*'])[0]['totalMatches'], 0)

    def test_invalid_or_excessive_queries_are_rejected_before_scanning(self):
        for queries in [[], ['a'] * 13, [''], ['x' * 161], [12], ['a\x00b']]:
            with self.subTest(queries=queries), self.assertRaises(ValueError):
                lookup(self.index([]), queries)

    def test_missing_columns_or_oversized_index_is_an_error_not_absence(self):
        for text in ['name\twebsite\nJane\thttps://jane.example\n', 'x' * (4 * 1024 * 1024 + 1)]:
            with self.subTest(size=len(text)), self.assertRaises(ValueError):
                lookup(text, ['Jane'])

    def test_one_row_matching_several_fields_counts_once_and_output_is_bounded(self):
        text = self.index([('Jane Smith', 'https://janesmith.example/', '', 'Jane Smith ' + 'x' * 2000)])
        result = lookup(text, ['Jane Smith'])
        self.assertEqual(result[0]['totalMatches'], 1)
        self.assertLessEqual(len(result[0]['matches'][0]['advertisedAddress']), 240)


if __name__ == '__main__':
    unittest.main()
