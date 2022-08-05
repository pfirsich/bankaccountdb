from dataclasses import dataclass
from datetime import datetime, date
from enum import Enum, auto
from pprint import pprint
from typing import Optional
import argparse
import json
import re
import sys
import xml.etree.ElementTree as ElementTree

# This module only parses camt.052 documents ("Bank to Customer Account Report")
# Specification (german, sorry): https://www.ebics.de/de/datenformate
# Anlage_3_Datenformate_3.6.pdf - "Bank To Customer Account Report", 7.2.3


class ParseError(Exception):
    pass


def strip_ns(tag):
    m = re.match(r"^(?:{([^}]+?)})?(.+)$", tag)
    assert m
    return m.group(2)


def parse_date_or_datetime(tree: ElementTree):
    if len(tree) != 1:
        raise ParseError("Maximum number of children in Dt is 1")
    if strip_ns(tree[0].tag) != "Dt" and strip_ns(tree[0].tag) != "DtTm":
        raise ParseError("Child of 'Dt' must be 'Dt' or 'DtTm'")
    return datetime.fromisoformat(tree[0].text)


def parse_generic_kv_list(tree: ElementTree):
    return {strip_ns(child.tag): child.text for child in tree}


@dataclass
class MessagePagination:
    page_number: int  # PgNb
    last_page_indication: bool  # LastPgInd

    @staticmethod
    def parse_xml(tree: ElementTree):
        page_number = None
        last_page_indication = None
        for child in tree:
            if strip_ns(child.tag) == "PgNb":
                page_number = int(child.text)
            elif strip_ns(child.tag) == "LastPgInd":
                last_page_indication = "true" in child.text.lower()
            else:
                raise ParseError(f"Unknown child '{child.tag} in MessagePagination")
        return MessagePagination(page_number, last_page_indication)

    def to_dict_tree(self):
        return {
            "pageNumber": self.page_number,
            "last_page_indication": self.last_page_indication,
        }


@dataclass
class GroupHeader:
    message_identification: str  # MsgId
    creation_time: datetime  # CreDtTm
    message_pagination: Optional[MessagePagination]  # MsgPgntn

    @staticmethod
    def parse_xml(tree: ElementTree):
        message_identification = None
        creation_time = None
        message_pagination = None
        for child in tree:
            if strip_ns(child.tag) == "MsgId":
                message_identification = child.text
            elif strip_ns(child.tag) == "CreDtTm":
                creation_time = datetime.fromisoformat(child.text)
            elif strip_ns(child.tag) == "MsgPgntn":
                message_pagination = MessagePagination.parse_xml(child)
            else:
                raise ParseError(f"Unknown child '{child.tag} in GroupHeader")
        return GroupHeader(message_identification, creation_time, message_pagination)

    def to_dict_tree(self):
        return {
            "messageIdentification": self.message_identification,
            "creationTime": self.creation_time.isoformat(),
            "messagePagination": self.message_pagination.to_dict_tree()
            if self.message_pagination
            else None,
        }


@dataclass
class FinancialInstitutionIdentification:
    bicfi: Optional[str]  # BIC
    name: Optional[str]  # Nm
    # actually GenericFinancialIdentification, but I can't find the spec for it
    other: Optional[dict]  # Othr

    @staticmethod
    def parse_xml(tree: ElementTree):
        bicfi = None
        name = None
        other = None
        for child in tree:
            if strip_ns(child.tag) == "BIC":
                bicfi = child.text
            elif strip_ns(child.tag) == "Nm":
                name = child.text
            elif strip_ns(child.tag) == "Othr":
                other = parse_generic_kv_list(child)
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in FinancialInstitutionIdentification"
                )
        return FinancialInstitutionIdentification(bicfi, name, other)

    def to_dict_tree(self):
        return {
            "bicfi": self.bicfi if self.bicfi else None,
            "name": self.name if self.name else None,
            "other": self.other if self.other else None,
        }


@dataclass
class Servicer:
    financial_institution_identification: FinancialInstitutionIdentification  # FinInstnId

    @staticmethod
    def parse_xml(tree: ElementTree):
        financial_institution_identification = None
        for child in tree:
            if strip_ns(child.tag) == "FinInstnId":
                financial_institution_identification = (
                    FinancialInstitutionIdentification.parse_xml(child)
                )
            else:
                raise ParseError(f"Unknown tag '{strip_ns(child.tag)}' in Servicer")
        return Servicer(financial_institution_identification)

    def to_dict_tree(self):
        return {
            "financialInstitutionIdentification": self.financial_institution_identification.to_dict_tree()
            if self.financial_institution_identification
            else None,
        }


@dataclass
class Account:
    # Actually AccountIdentification with other options, but I don't want to worry about that
    iban: str  # Id/IBAN
    currency: Optional[str]  # Ccy
    servicer: Optional[Servicer]  # Svcr

    @staticmethod
    def parse_xml(tree: ElementTree):
        iban = None
        currency = None
        servicer = None
        for child in tree:
            if strip_ns(child.tag) == "Id":
                assert strip_ns(child[0].tag) == "IBAN"
                iban = child[0].text
            elif strip_ns(child.tag) == "Ccy":
                currency = child.text
            elif strip_ns(child.tag) == "Svcr":
                servicer = Servicer.parse_xml(child)
            else:
                raise ParseError(f"Unknown tag '{strip_ns(child.tag)}' in Account")
        return Account(iban, currency, servicer)

    def to_dict_tree(self):
        return {
            "iban": self.iban,
            "currency": self.currency if self.currency else None,
            "servicer": self.servicer.to_dict_tree() if self.servicer else None,
        }


class BalanceType(Enum):
    # All sources list different ones, I just give up
    ClosingAvailable = "CLAV"
    ClosingBooked = "CLBD"
    ForwardAvailable = "FWAV"
    OpeningBooked = "OPBD"
    PreviouslyClosed = "PRCD"
    OpeningAvailable = "OPAV"

    @staticmethod
    def parse_xml(tree: ElementTree):
        assert (
            len(tree) == 1
            and strip_ns(tree[0].tag) == "CdOrPrtry"
            and len(tree[0]) == 1
            and strip_ns(tree[0][0].tag) == "Cd"
        )
        return BalanceType(tree[0][0].text)


class CreditDebit(Enum):
    Credit = "CRDT"
    Debit = "DBIT"


@dataclass
class Amount:
    value: float  # I know you should not represent money as floats, but it's fine
    currency: str

    def parse_xml(tree: ElementTree):
        return Amount(float(tree.text), tree.attrib["Ccy"])

    def to_dict_tree(self):
        return {"value": self.value, "currency": self.currency}


@dataclass
class Balance:
    # actually a nested thing
    balance_type: BalanceType  # Tp
    amount: Amount  # Amt
    credit_debit: CreditDebit  # CdtDbtInd
    date: datetime  # Dt

    @staticmethod
    def parse_xml(tree: ElementTree):
        balance_type = None
        amount = None
        credit_debit = None
        date = None
        for child in tree:
            if strip_ns(child.tag) == "Tp":
                balance_type = BalanceType.parse_xml(child)
            elif strip_ns(child.tag) == "Amt":
                amount = Amount.parse_xml(child)
            elif strip_ns(child.tag) == "CdtDbtInd":
                credit_debit = CreditDebit(child.text)
            elif strip_ns(child.tag) == "Dt":
                date = parse_date_or_datetime(child)
            else:
                raise ParseError(f"Unknown tag '{strip_ns(child.tag)}' in Balance")
        return Balance(balance_type, amount, credit_debit, date)

    def to_dict_tree(self):
        return {
            "balanceType": str(self.balance_type),
            "amount": self.amount.to_dict_tree(),
            "creditDebit": str(self.credit_debit),
            "date": self.date.isoformat(),
        }


class EntryStatus(Enum):
    Booked = "BOOK"
    Information = "INFO"
    Pending = "PDNG"
    Future = "FUTR"


# Don't understand the reference on this, so I'll wing it
@dataclass
class ProprietaryReference:
    reference_type: str  # Tp
    reference: str  # Ref

    @staticmethod
    def parse_xml(tree: ElementTree):
        reference_type = None
        reference = None
        for child in tree:
            if strip_ns(child.tag) == "Tp":
                reference_type = child.text
            elif strip_ns(child.tag) == "Ref":
                reference = child.text
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in ProprietaryReference"
                )
        return ProprietaryReference(reference_type, reference)

    def to_dict_tree(self):
        return {"type": self.reference_type, "reference": self.reference}


@dataclass
class References:
    end_to_end_identification: Optional[str]  # EndToEndId
    mandate_identification: Optional[str]  # MndtId
    proprietary_reference: list[ProprietaryReference]  # Prtry

    @staticmethod
    def parse_xml(tree: ElementTree):
        end_to_end_identification = None
        mandate_identification = None
        proprietary_reference = []
        for child in tree:
            if strip_ns(child.tag) == "EndToEndId":
                end_to_end_identification = child.text
            elif strip_ns(child.tag) == "MndtId":
                mandate_identification = child.text
            elif strip_ns(child.tag) == "Prtry":
                proprietary_reference.append(ProprietaryReference.parse_xml(child))
            else:
                raise ParseError(f"Unknown tag '{strip_ns(child.tag)}' in References")
        return References(
            end_to_end_identification, mandate_identification, proprietary_reference
        )

    def to_dict_tree(self):
        return {
            "end_to_end_identification": self.end_to_end_identification
            if self.end_to_end_identification
            else None,
            "mandate_identification": self.mandate_identification
            if self.mandate_identification
            else None,
            "proprietaryReference": [
                r.to_dict_tree() for r in self.proprietary_reference
            ],
        }


@dataclass
class ProprietaryBankTransactionCode:  # only supports Proprietary (Prtry)
    code: str  # Cd
    issuer: str  # Issr

    @staticmethod
    def parse_xml(tree: ElementTree):
        code = None
        issuer = None
        for child in tree:
            if strip_ns(child.tag) == "Cd":
                code = child.text
            elif strip_ns(child.tag) == "Issr":
                issuer = child.text
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in ProprietaryBankTransactionCode"
                )
        return ProprietaryBankTransactionCode(code, issuer)

    def to_dict_tree(self):
        return {"code": self.code, "issuer": self.issuer}


BankTransactionCode = ProprietaryBankTransactionCode  # | DomainBankTransactionCode


def parse_bank_transaction_code_from_xml(tree: ElementTree):
    assert len(tree) == 1
    if strip_ns(tree[0].tag) == "Prtry":
        return ProprietaryBankTransactionCode.parse_xml(tree[0])
    else:
        raise ParseError(f"Unknown tag '{strip_ns(tree.tag)}' in BankTransactionCode")


@dataclass
class PrivateIdentification:
    # Actually GenericPersonIdentification
    other: Optional[dict]  # Othr

    @staticmethod
    def parse_xml(tree: ElementTree):
        other = None
        for child in tree:
            if strip_ns(child.tag) == "Othr":
                other = parse_generic_kv_list(child)
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in PrivateIdentification"
                )
        return PrivateIdentification(other)

    def to_dict_tree(self):
        return {"other": self.other if self.other else None}


# Again much larger, but I only add what I need
Identification = PrivateIdentification  # (PrvtId) | OrganisationIdentification (OrgId)


def parse_identification_from_xml(tree: ElementTree):
    assert len(tree) == 1
    if strip_ns(tree[0].tag) == "PrvtId":
        return PrivateIdentification.parse_xml(tree[0])
    else:
        raise ParseError(f"Unknown tag '{strip_ns(tree.tag)}' in Identification")


@dataclass
class PartyIdentification:
    name: Optional[str]  # Nm
    identification: Optional[Identification]  # Id

    @staticmethod
    def parse_xml(tree: ElementTree):
        name = None
        identification = None
        for child in tree:
            if strip_ns(child.tag) == "Nm":
                name = child.text
            elif strip_ns(child.tag) == "Id":
                identification = parse_identification_from_xml(child)
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in PartyIdentification"
                )
        return PartyIdentification(name, identification)

    def to_dict_tree(self):
        return {
            "name": self.name if self.name else None,
            "identification": self.identification.to_dict_tree()
            if self.identification
            else None,
        }


PartyChoice = PartyIdentification | FinancialInstitutionIdentification

# This can't be right
def parse_partchoice_from_xml(tree: ElementTree):
    if len(tree) == 1 and strip_ns(tree[0].tag) == "FinInstnId":
        return FinancialInstitutionIdentification.parse_xml(tree[0])
    else:
        return PartyIdentification.parse_xml(tree)


# This is much bigger in the reference, but I only add what I need
@dataclass
class CashAccount:
    iban: str  # Id/IBAN

    @staticmethod
    def parse_xml(tree: ElementTree):
        iban = None
        for child in tree:
            if strip_ns(child.tag) == "Id":
                assert strip_ns(child[0].tag) == "IBAN"
                iban = child[0].text
            else:
                raise ParseError(f"Unknown tag '{strip_ns(child.tag)}' in CashAccount")
        return CashAccount(iban)

    def to_dict_tree(self):
        return {"iban": self.iban}


@dataclass
class RelatedParties:
    debtor: Optional[PartyChoice]  # Dbtr
    debtor_account: Optional[CashAccount]  # DbtrAcct
    creditor: Optional[PartyChoice]  # Cdtr
    creditor_account: Optional[CashAccount]  # CdtrAcct

    @staticmethod
    def parse_xml(tree: ElementTree):
        debtor = None
        debtor_account = None
        creditor = None
        creditor_account = None
        for child in tree:
            if strip_ns(child.tag) == "Dbtr":
                debtor = parse_partchoice_from_xml(child)
            elif strip_ns(child.tag) == "DbtrAcct":
                debtor_account = CashAccount.parse_xml(child)
            elif strip_ns(child.tag) == "Cdtr":
                creditor = parse_partchoice_from_xml(child)
            elif strip_ns(child.tag) == "CdtrAcct":
                creditor_account = CashAccount.parse_xml(child)
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in RelatedParties"
                )
        return RelatedParties(debtor, debtor_account, creditor, creditor_account)

    def to_dict_tree(self):
        return {
            "debtor": self.debtor.to_dict_tree() if self.debtor else None,
            "debtorAccount": self.debtor_account.to_dict_tree()
            if self.debtor_account
            else None,
            "creditor": self.creditor.to_dict_tree() if self.creditor else None,
            "creditorAccount": self.creditor_account.to_dict_tree()
            if self.creditor_account
            else None,
        }


@dataclass
class RelatedAgents:
    debtor_agent: Optional[FinancialInstitutionIdentification]  # DbtrAgt
    creditor_agent: Optional[FinancialInstitutionIdentification]  # CdtrAgt

    @staticmethod
    def parse_xml(tree: ElementTree):
        debtor_agent = None
        creditor_agent = None
        for child in tree:
            if strip_ns(child.tag) == "DbtrAgt":
                assert len(child[0]) == 1 and strip_ns(child[0].tag) == "FinInstnId"
                debtor_agent = FinancialInstitutionIdentification.parse_xml(child[0])
            elif strip_ns(child.tag) == "CdtrAgt":
                assert len(child[0]) == 1 and strip_ns(child[0].tag) == "FinInstnId"
                creditor_agent = FinancialInstitutionIdentification.parse_xml(child[0])
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in RelatedAgents"
                )
        return RelatedAgents(debtor_agent, creditor_agent)

    def to_dict_tree(self):
        return {
            "debtorAgent": self.debtor_agent.to_dict_tree(),
            "creditorAgent": self.creditor_agent.to_dict_tree(),
        }


# This this is actually HUGE
@dataclass
class RelatedRemittanceInformation:
    unstructured: str  # Ustrd

    @staticmethod
    def parse_xml(tree: ElementTree):
        unstructured = None
        for child in tree:
            if strip_ns(child.tag) == "Ustrd":
                unstructured = child.text
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in RelatedRemittanceInformation"
                )
        return RelatedRemittanceInformation(unstructured)

    def to_dict_tree(self):
        return {"unstructured": self.unstructured}


@dataclass
class TransactionDetails:
    references: Optional[References]  # Refs
    bank_transaction_code: Optional[BankTransactionCode]  # BkTxCd
    related_parties: Optional[RelatedParties]  # RltdPties
    related_agents: Optional[RelatedAgents]  # RltdAgts
    related_remittance_information: list[RelatedRemittanceInformation]  # RmtInf

    @staticmethod
    def parse_xml(tree: ElementTree):
        references = None
        bank_transaction_code = None
        related_parties = None
        related_agent = None
        related_remittance_information = []
        for child in tree:
            if strip_ns(child.tag) == "Refs":
                references = References.parse_xml(child)
            elif strip_ns(child.tag) == "BkTxCd":
                bank_transaction_code = parse_bank_transaction_code_from_xml(child)
            elif strip_ns(child.tag) == "RltdPties":
                related_parties = RelatedParties.parse_xml(child)
            elif strip_ns(child.tag) == "RltdAgts":
                related_agents = RelatedAgents.parse_xml(child)
            elif strip_ns(child.tag) == "RmtInf":
                related_remittance_information.append(
                    RelatedRemittanceInformation.parse_xml(child)
                )
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in TransactionDetails"
                )
        return TransactionDetails(
            references,
            bank_transaction_code,
            related_parties,
            related_agents,
            related_remittance_information,
        )

    def to_dict_tree(self):
        return {
            "references": self.references.to_dict_tree() if self.references else None,
            "bankTransactionCode": self.bank_transaction_code.to_dict_tree()
            if self.bank_transaction_code
            else None,
            "relatedAgents": self.related_agents.to_dict_tree()
            if self.related_agents
            else None,
            "relatedParties": self.related_parties.to_dict_tree()
            if self.related_parties
            else None,
            "relatedRemittanceInformation": [
                r.to_dict_tree() for r in self.related_remittance_information
            ],
        }


@dataclass
class EntryDetails:
    transaction_details: TransactionDetails  # TxDtls

    @staticmethod
    def parse_xml(tree: ElementTree):
        transaction_details = None
        for child in tree:
            if strip_ns(child.tag) == "TxDtls":
                transaction_details = TransactionDetails.parse_xml(child)
            else:
                raise ParseError(f"Unknown tag '{strip_ns(child.tag)}' in EntryDetails")
        return EntryDetails(transaction_details)

    def to_dict_tree(self):
        return {"transactionDetails": self.transaction_details.to_dict_tree()}


@dataclass
class Entry:
    amount: Amount  # Amt
    credit_debit: CreditDebit  # CdtDbtInd
    status: EntryStatus  # Sts
    booking_date: Optional[datetime]  # BookgDt
    value_date: Optional[datetime]  # ValDt
    account_service_reference: Optional[str]  # AcctSvcrRef
    # <BkTxCd/> <- why?
    details: list[EntryDetails]  # NtryDtls
    additional_information: Optional[str]  # AddtlNtryInf

    @staticmethod
    def parse_xml(tree: ElementTree):
        amount = None
        credit_debit = None
        status = None
        booking_date = None
        value_date = None
        account_service_reference = None
        details = []
        additional_information = None
        for child in tree:
            if strip_ns(child.tag) == "Amt":
                amount = Amount.parse_xml(child)
            elif strip_ns(child.tag) == "CdtDbtInd":
                credit_debit = CreditDebit(child.text)
            elif strip_ns(child.tag) == "Sts":
                status = EntryStatus(child.text)
            elif strip_ns(child.tag) == "BookgDt":
                booking_date = parse_date_or_datetime(child)
            elif strip_ns(child.tag) == "ValDt":
                value_date = parse_date_or_datetime(child)
            elif strip_ns(child.tag) == "AcctSvcrRef":
                account_service_reference = child.text
            elif strip_ns(child.tag) == "NtryDtls":
                details.append(EntryDetails.parse_xml(child))
            elif strip_ns(child.tag) == "AddtlNtryInf":
                additional_information = child.text
            elif strip_ns(child.tag) == "BkTxCd":
                # There is just a stray "<BkTxCd/>" in there and I don't know why
                # I don't think the spec mentions it either
                pass
            else:
                raise ParseError(f"Unknown tag '{strip_ns(child.tag)}' in Entry")
        return Entry(
            amount,
            credit_debit,
            status,
            booking_date,
            value_date,
            account_service_reference,
            details,
            additional_information,
        )

    def to_dict_tree(self):
        return {
            "amount": self.amount.to_dict_tree(),
            "creditDebit": str(self.credit_debit),
            "status": str(self.status),
            "bookingDate": self.booking_date.isoformat() if self.booking_date else None,
            "valueDate": self.value_date.isoformat() if self.value_date else None,
            "accountServiceReference": self.account_service_reference,
            "details": [d.to_dict_tree() for d in self.details],
            "additionalInformation": self.additional_information,
        }


@dataclass
class Report:
    identification: str  # Id
    eletronic_sequence_number: Optional[int]  # ElctrncSeqNb
    creation_time: Optional[datetime]  # CreDtTm
    account: Account  # Acct
    balances: list[Balance]  # Bal
    entries: list[Entry]  # Ntry

    @staticmethod
    def parse_xml(tree: ElementTree):
        identification = None
        eletronic_sequence_number = None
        creation_time = None
        account = None
        balances = []
        entries = []
        for child in tree:
            if strip_ns(child.tag) == "Id":  # Group Header
                identification = child.text
            elif strip_ns(child.tag) == "ElctrncSeqNb":
                eletronic_sequence_number = int(child.text, base=10)
            elif strip_ns(child.tag) == "CreDtTm":
                creation_time = datetime.fromisoformat(child.text)
            elif strip_ns(child.tag) == "Acct":
                account = Account.parse_xml(child)
            elif strip_ns(child.tag) == "Bal":
                balances.append(Balance.parse_xml(child))
            elif strip_ns(child.tag) == "Ntry":
                entries.append(Entry.parse_xml(child))
            else:
                raise ParseError(f"Unknown tag '{strip_ns(child.tag)}' in Report")
        return Report(
            identification,
            eletronic_sequence_number,
            creation_time,
            account,
            balances,
            entries,
        )

    def to_dict_tree(self):
        return {
            "identification": self.identification,
            "eletronicSequenceNumber": self.eletronic_sequence_number,
            "creationTime": self.creation_time.isoformat()
            if self.creation_time
            else None,
            "account": self.account.to_dict_tree(),
            "balances": [b.to_dict_tree() for b in self.balances],
            "entries": [e.to_dict_tree() for e in self.entries],
        }


@dataclass
class BankToCustomerAccountReport:
    group_header: GroupHeader  # GrpHdr
    reports: list[Report]  # Rpt

    @staticmethod
    def parse_xml(tree: ElementTree):
        group_header = None
        reports = []
        for child in tree:
            if strip_ns(child.tag) == "GrpHdr":  # Group Header
                group_header = GroupHeader.parse_xml(child)
            elif strip_ns(child.tag) == "Rpt":
                reports.append(Report.parse_xml(child))
            else:
                raise ParseError(
                    f"Unknown tag '{strip_ns(child.tag)}' in BkToCstmrAcctRpt"
                )
        return BankToCustomerAccountReport(group_header, reports)

    def to_dict_tree(self):
        return {
            "groupHeader": self.group_header.to_dict_tree(),
            "reports": [r.to_dict_tree() for r in self.reports],
        }


def parse_etree(tree: ElementTree) -> BankToCustomerAccountReport:
    root = tree.getroot()

    if strip_ns(root.tag) != "Document":
        raise ParseError("Root must be 'Document'")

    if len(root) != 1 or strip_ns(root[0].tag) != "BkToCstmrAcctRpt":
        raise ParseError("Document must be BkToCstmrAcctRpt")

    return BankToCustomerAccountReport.parse_xml(root[0])


def parse_file(path: str) -> BankToCustomerAccountReport:
    return parse_etree(ElementTree.parse(path))


def parse_string(data: str) -> BankToCustomerAccountReport:
    return parse_etree(ElementTree.fromstring(data))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    args = parser.parse_args()

    report = parse_file(args.file)
    dict_tree = report.to_dict_tree()
    print(json.dumps(dict_tree, indent=4))


if __name__ == "__main__":
    main()
