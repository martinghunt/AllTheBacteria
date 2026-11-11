import unittest

from atb_release_metadata.ncbi_alias_audit import parse_records, search_result


class NcbiAliasAuditTest(unittest.TestCase):
    def test_only_explicit_run_identifiers_are_aliases(self):
        xml=b'''<EXPERIMENT_PACKAGE_SET><EXPERIMENT_PACKAGE>
          <EXPERIMENT accession="SRX1"><TITLE>mentions SRR999</TITLE></EXPERIMENT>
          <SAMPLE><IDENTIFIERS><EXTERNAL_ID namespace="BioSample">SAMN111</EXTERNAL_ID></IDENTIFIERS>
          <SAMPLE_NAME><SCIENTIFIC_NAME>Species</SCIENTIFIC_NAME></SAMPLE_NAME></SAMPLE>
          <RUN_SET><RUN accession="SRR456" is_public="true" total_bases="123">
          <IDENTIFIERS><PRIMARY_ID>SRR456</PRIMARY_ID><SECONDARY_ID>SRR123</SECONDARY_ID>
          <SUBMITTER_ID>SRR777</SUBMITTER_ID></IDENTIFIERS></RUN></RUN_SET>
          </EXPERIMENT_PACKAGE></EXPERIMENT_PACKAGE_SET>'''
        record=parse_records(xml)[0]
        self.assertEqual(record['primary_run'],'SRR456')
        self.assertEqual(record['secondary_runs'],['SRR123'])
        self.assertEqual(record['biosamples'],['SAMN111'])
        self.assertEqual(record['total_bases'],'123')

    def test_run_pool_sample_takes_precedence(self):
        xml=b'''<EXPERIMENT_PACKAGE_SET><EXPERIMENT_PACKAGE>
          <SAMPLE><IDENTIFIERS><EXTERNAL_ID namespace="BioSample">SAMN111</EXTERNAL_ID></IDENTIFIERS></SAMPLE>
          <RUN_SET><RUN accession="SRR456"><Pool><Member><IDENTIFIERS>
          <EXTERNAL_ID namespace="BioSample">SAMN222</EXTERNAL_ID>
          </IDENTIFIERS></Member></Pool></RUN></RUN_SET>
          </EXPERIMENT_PACKAGE></EXPERIMENT_PACKAGE_SET>'''
        self.assertEqual(parse_records(xml)[0]['biosamples'],['SAMN222'])

    def test_truncated_and_error_responses_are_not_no_hits(self):
        with self.assertRaises(ValueError):
            search_result(b'{"esearchresult":{"count":"2","idlist":["1"]}}')
        with self.assertRaises(ValueError):
            search_result(b'{"error":"API rate limit exceeded"}')
        with self.assertRaises(ValueError):
            parse_records(b'<ERROR>temporarily unavailable</ERROR>')
        self.assertEqual(search_result(b'{"esearchresult":{"count":"0","idlist":[]}}')['idlist'],[])
