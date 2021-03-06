from common.serializers.serialization import domain_state_serializer
from plenum.common.constants import TARGET_NYM, TXN_TYPE, RAW, DATA, \
    ROLE, VERKEY, TXN_TIME, NYM, NAME, VERSION, ORIGIN
from plenum.common.types import f
from plenum.test.helper import waitForSufficientRepliesForRequests, \
    getRepliesFromClientInbox

from indy_client.test.state_proof.helper import check_valid_proof
from indy_common.constants import GET_ATTR, GET_NYM, SCHEMA, GET_SCHEMA, ATTR_NAMES, REF, SIGNATURE_TYPE, CLAIM_DEF, \
    REVOCATION, GET_CLAIM_DEF

from indy_common.serialization import attrib_raw_data_serializer

# Fixtures, do not remove
from indy_node.test.helper import addAttributeAndCheck
from indy_client.client.wallet.attribute import Attribute, LedgerStore
from indy_client.test.test_nym_attrib import \
    addedRawAttribute, attributeName, attributeValue, attributeData


def test_state_proof_returned_for_get_attr(looper,
                                           addedRawAttribute,
                                           attributeName,
                                           attributeData,
                                           trustAnchor,
                                           trustAnchorWallet):
    """
    Tests that state proof is returned in the reply for GET_ATTR transactions
    """
    client = trustAnchor
    get_attr_operation = {
        TARGET_NYM: addedRawAttribute.dest,
        TXN_TYPE: GET_ATTR,
        RAW: attributeName
    }
    get_attr_request = trustAnchorWallet.signOp(get_attr_operation)
    trustAnchorWallet.pendRequest(get_attr_request)
    pending = trustAnchorWallet.preparePending()
    client.submitReqs(*pending)
    waitForSufficientRepliesForRequests(looper, trustAnchor, requests=pending)
    replies = getRepliesFromClientInbox(client.inBox, get_attr_request.reqId)
    expected_data = attrib_raw_data_serializer.deserialize(attributeData)
    for reply in replies:
        result = reply['result']
        assert DATA in result
        data = attrib_raw_data_serializer.deserialize(result[DATA])
        assert data == expected_data
        assert result[TXN_TIME]
        check_valid_proof(reply, client)


def test_state_proof_returned_for_get_nym(looper,
                                          trustAnchor,
                                          trustAnchorWallet,
                                          userWalletA):
    """
    Tests that state proof is returned in the reply for GET_NYM transactions
    """
    client = trustAnchor
    dest = userWalletA.defaultId

    nym = {
        TARGET_NYM: dest,
        TXN_TYPE: NYM
    }
    nym_request = trustAnchorWallet.signOp(nym)
    trustAnchorWallet.pendRequest(nym_request)
    pending = trustAnchorWallet.preparePending()
    client.submitReqs(*pending)

    get_nym_operation = {
        TARGET_NYM: dest,
        TXN_TYPE: GET_NYM
    }

    get_nym_request = trustAnchorWallet.signOp(get_nym_operation)
    trustAnchorWallet.pendRequest(get_nym_request)
    pending = trustAnchorWallet.preparePending()
    client.submitReqs(*pending)
    waitForSufficientRepliesForRequests(looper, trustAnchor, requests=pending)
    replies = getRepliesFromClientInbox(client.inBox, get_nym_request.reqId)

    for reply in replies:
        result = reply['result']
        assert DATA in result
        assert result[DATA]
        data = domain_state_serializer.deserialize(result[DATA])
        assert ROLE in data
        assert VERKEY in data
        assert f.IDENTIFIER.nm in data
        assert result[TXN_TIME]
        check_valid_proof(reply, client)


def test_state_proof_returned_for_get_schema(looper,
                                             trustAnchor,
                                             trustAnchorWallet):
    """
    Tests that state proof is returned in the reply for GET_NYM transactions
    """
    client = trustAnchor
    dest = trustAnchorWallet.defaultId
    schema_name = "test_schema"
    schema_version = "1.0"
    schema_attr_names = ["width", "height"]
    data = {
        NAME: schema_name,
        VERSION: schema_version,
        ATTR_NAMES: schema_attr_names
    }
    schema_operation = {
        TXN_TYPE: SCHEMA,
        DATA: data
    }
    nym_request = trustAnchorWallet.signOp(schema_operation)
    trustAnchorWallet.pendRequest(nym_request)
    pending = trustAnchorWallet.preparePending()
    client.submitReqs(*pending)
    waitForSufficientRepliesForRequests(looper, trustAnchor, requests=pending)
    get_schema_operation = {
        TARGET_NYM: dest,
        TXN_TYPE: GET_SCHEMA,
        DATA: {
            NAME: schema_name,
            VERSION: schema_version,
        }
    }
    get_schema_request = trustAnchorWallet.signOp(get_schema_operation)
    trustAnchorWallet.pendRequest(get_schema_request)
    pending = trustAnchorWallet.preparePending()
    client.submitReqs(*pending)
    waitForSufficientRepliesForRequests(looper, trustAnchor, requests=pending)
    replies = getRepliesFromClientInbox(client.inBox, get_schema_request.reqId)
    for reply in replies:
        result = reply['result']
        assert DATA in result
        data = result.get(DATA)
        assert data
        assert ATTR_NAMES in data
        assert data[ATTR_NAMES] == schema_attr_names
        assert NAME in data
        assert VERSION in data
        assert result[TXN_TIME]
        check_valid_proof(reply, client)


def test_state_proof_returned_for_get_claim_def(looper,
                                                trustAnchor,
                                                trustAnchorWallet):
    """
    Tests that state proof is returned in the reply for GET_NYM transactions
    """
    client = trustAnchor
    dest = trustAnchorWallet.defaultId
    data = {"primary": {'N': '123'}, REVOCATION: {'h0': '456'}}
    claim_def_operation = {
        TXN_TYPE: CLAIM_DEF,
        REF: 12,
        DATA: data,
        SIGNATURE_TYPE: 'CL'
    }
    nym_request = trustAnchorWallet.signOp(claim_def_operation)
    trustAnchorWallet.pendRequest(nym_request)
    pending = trustAnchorWallet.preparePending()
    client.submitReqs(*pending)
    waitForSufficientRepliesForRequests(looper, trustAnchor, requests=pending)
    get_claim_def_operation = {
        ORIGIN: dest,
        TXN_TYPE: GET_CLAIM_DEF,
        REF: 12,
        SIGNATURE_TYPE: 'CL'
    }
    get_schema_request = trustAnchorWallet.signOp(get_claim_def_operation)
    trustAnchorWallet.pendRequest(get_schema_request)
    pending = trustAnchorWallet.preparePending()
    client.submitReqs(*pending)
    waitForSufficientRepliesForRequests(looper, trustAnchor, requests=pending)
    replies = getRepliesFromClientInbox(client.inBox, get_schema_request.reqId)
    expected_data = data
    for reply in replies:
        result = reply['result']
        assert DATA in result
        data = result.get(DATA)
        assert data
        assert data == expected_data
        assert result[TXN_TIME]
        check_valid_proof(reply, client)
