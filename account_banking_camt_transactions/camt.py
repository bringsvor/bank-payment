__author__ = 'tbri'

from lxml import etree
from openerp import api, models, exceptions, _
from openerp.addons.account_banking.parsers import models
from openerp.addons.account_banking.parsers.convert import str2date

bt = models.mem_bank_transaction


class transaction(models.mem_bank_transaction):

    def __init__(self, values, *args, **kwargs):
        super(transaction, self).__init__(*args, **kwargs)
        for attr in values:
            setattr(self, attr, values[attr])

    def is_valid(self):
        return not self.error_message


class parser(models.parser):
    code = 'CAMT054'
    country_code = 'NO'
    name = 'CAMT 054 Format'
    doc = '''\
CAMT 054 Format parser
'''

    def parse_Ntfctn(self, cr, node):
        statement = models.mem_bank_statement()

        for Ntry in self.xpath(node, './ns:Ntry'):
            transaction_detail = self.parse_Ntry(Ntry)
            statement.transactions.append(
                transaction(transaction_detail))
        return statement




    def parse_Ntry(self, node):
        """
        :param node: Ntry node
        """
        entry_details = {
            'execution_date': self.xpath(node, './ns:BookgDt/ns:Dt')[0].text,
            'value_date': self.xpath(node, './ns:ValDt/ns:Dt')[0].text,
            'transfer_type': self.get_transfer_type(node),
            'transferred_amount': self.parse_amount(node)
            }
        TxDtls = self.xpath(node, './ns:NtryDtls/ns:TxDtls')
        if len(TxDtls) == 1:
            vals = self.parse_TxDtls(TxDtls[0], entry_details)
        else:
            vals = entry_details
        return vals

    def get_party_values(self, TxDtls):
        """
        Determine to get either the debtor or creditor party node
        and extract the available data from it
        """
        vals = {}
        party_type = self.find(
            TxDtls, '../../ns:CdtDbtInd').text == 'CRDT' and 'Dbtr' or 'Cdtr'
        party_node = self.find(TxDtls, './ns:RltdPties/ns:%s' % party_type)
        account_node = self.find(
            TxDtls, './ns:RltdPties/ns:%sAcct/ns:Id' % party_type)
        bic_node = self.find(
            TxDtls,
            './ns:RltdAgts/ns:%sAgt/ns:FinInstnId/ns:BIC' % party_type)
        if party_node is not None:
            name_node = self.find(party_node, './ns:Nm')
            vals['remote_owner'] = (
                name_node.text if name_node is not None else False)
            country_node = self.find(party_node, './ns:PstlAdr/ns:Ctry')
            vals['remote_owner_country'] = (
                country_node.text if country_node is not None else False)
            address_node = self.find(party_node, './ns:PstlAdr/ns:AdrLine')
            if address_node is not None:
                vals['remote_owner_address'] = [address_node.text]
        if account_node is not None:
            iban_node = self.find(account_node, './ns:IBAN')
            if iban_node is not None:
                vals['remote_account'] = iban_node.text
                if bic_node is not None:
                    vals['remote_bank_bic'] = bic_node.text
            else:
                domestic_node = self.find(account_node, './ns:Othr/ns:Id')
                vals['remote_account'] = (
                    domestic_node.text if domestic_node is not None else False)
        return vals

    def parse_amount(self, node):
        """
        Parse an element that contains both Amount and CreditDebitIndicator

        :return: signed amount
        :returntype: float
        """
        sign = -1 if node.find(self.ns + 'CdtDbtInd').text == 'DBIT' else 1
        return sign * float(node.find(self.ns + 'Amt').text)


    def parse_TxDtls(self, TxDtls, entry_values):
        """
        Parse a single TxDtls node
        """
        vals = dict(entry_values)
        unstructured = self.xpath(TxDtls, './ns:RmtInf/ns:Ustrd')
        if unstructured:
            vals['message'] = ' '.join([x.text for x in unstructured])
        structured = self.find(
            TxDtls, './ns:RmtInf/ns:Strd/ns:CdtrRefInf/ns:Ref')
        if structured is None or not structured.text:
            structured = self.find(TxDtls, './ns:Refs/ns:EndToEndId')
        if structured is not None:
            vals['reference'] = structured.text
        else:
            if vals.get('message'):
                vals['reference'] = vals['message']
        vals.update(self.get_party_values(TxDtls))
        return vals


    def tag(self, node):
        """
        Return the tag of a node, stripped from its namespace
        """
        return node.tag[len(self.ns):]

    def assert_tag(self, node, expected):
        """
        Get node's stripped tag and compare with expected
        """
        assert self.tag(node) == expected, (
            "Expected tag '%s', got '%s' instead" %
            (self.tag(node), expected))

    def xpath(self, node, expr):
        """
        Wrap namespaces argument into call to Element.xpath():

        self.xpath(node, './ns:Acct/ns:Id')
        """
        return node.xpath(expr, namespaces={'ns': self.ns[1:-1]})

    def find(self, node, expr):
        """
        Like xpath(), but return first result if any or else False

        Return None to test nodes for being truesy
        """
        result = node.xpath(expr, namespaces={'ns': self.ns[1:-1]})
        if result:
            return result[0]
        return None


    def get_transfer_type(self, node):
        """
        Map entry descriptions to transfer types. To extend with
        proper mapping from BkTxCd/Domn/Cd/Fmly/Cd to transfer types
        if we can get our hands on real life samples.

        For now, leave as a hook for bank specific overrides to map
        properietary codes from BkTxCd/Prtry/Cd.

        :param node: Ntry node
        """
        return bt.ORDER


    def check_version(self):
        """
        Sanity check the document's namespace
        """

        if self.ns.startswith('{urn:iso:std:iso:20022:tech:xsd:camt.054.'):
            return True # 054...

        raise Warning(
                "Error",
                "Only CAMT.053 is supported at the moment.")



    def parse(self, cr, data):
        """
        Parse a CAMT053 XML file
        """
        root = etree.fromstring(data)
        self.ns = root.tag[:root.tag.index("}") + 1]
        self.check_version()
        self.assert_tag(root[0][0], 'GrpHdr')
        statements = []
        for node in root[0][1:]:
            statement = self.parse_Ntfctn(cr, node)
            if len(statement.transactions):
                statements.append(statement)
        return statements
