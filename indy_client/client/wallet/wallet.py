import datetime
import json
import operator
from collections import OrderedDict
from collections import deque
from typing import List
from typing import Optional

from jsonpickle import tags

from ledger.util import F
from plenum.client.wallet import Wallet as PWallet, getClassVersionKey
from plenum.common.did_method import DidMethods
from plenum.common.util import randomString
from stp_core.common.log import getlogger
from plenum.common.constants import TXN_TYPE, TARGET_NYM, DATA, \
    IDENTIFIER, NYM, ROLE, VERKEY, NODE, NAME, VERSION, ORIGIN, CURRENT_PROTOCOL_VERSION
from plenum.common.types import f

from indy_client.client.wallet.attribute import Attribute, AttributeKey, \
    LedgerStore
from indy_client.client.wallet.connection import Connection
from indy_client.client.wallet.node import Node
from indy_client.client.wallet.trustAnchoring import TrustAnchoring
from indy_client.client.wallet.upgrade import Upgrade
from indy_client.client.wallet.pool_config import PoolConfig
from indy_common.did_method import DefaultDidMethods
from indy_common.exceptions import ConnectionNotFound
from indy_common.types import Request
from indy_common.identity import Identity
from indy_common.constants import ATTRIB, GET_TXNS, GET_ATTR, \
    GET_NYM, POOL_UPGRADE, GET_SCHEMA, GET_CLAIM_DEF, REF, SIGNATURE_TYPE, POOL_CONFIG
from stp_core.types import Identifier

ENCODING = "utf-8"

logger = getlogger()


def getClassVersionKeyBeforeRebranding(cls):
    return getClassVersionKey(cls).replace('indy_client', 'sovrin_client')


class WalletRawUpdaterToVersion1:

    _LINK_FIELD_RENAMINGS = {
        'linkStatus': 'connection_status',
        'linkLastSynced': 'connection_last_synced',
        'linkLastSyncNo': 'connection_last_sync_no',
        'invitationNonce': 'request_nonce',

        # rule for the intermediate renaming state (MGL version)
        'connectionLastSynced': 'connection_last_synced'
    }

    @classmethod
    def _processLink(cls, link):
        link[tags.OBJECT] = \
            'sovrin_client.client.wallet.connection.Connection'
        for key in link:
            if key in cls._LINK_FIELD_RENAMINGS:
                link[cls._LINK_FIELD_RENAMINGS[key]] = link.pop(key)

    @classmethod
    def _traverseDict(cls, d):
        if d.get(tags.OBJECT) == 'sovrin_client.client.wallet.link.Link':
            cls._processLink(d)

        for key in d:
            cls._traverseObject(d[key])

    @classmethod
    def _traverseList(cls, l):
        for item in l:
            cls._traverseObject(item)

    @classmethod
    def _traverseObject(cls, v):
        if isinstance(v, dict):
            cls._traverseDict(v)
        elif isinstance(v, list):
            cls._traverseList(v)

    @classmethod
    def update(cls, raw):
        if '_links' in raw:
            raw['_connections'] = raw.pop('_links')

        cls._traverseDict(raw)

        raw[getClassVersionKeyBeforeRebranding(Wallet)] = 1


class WalletRawUpdaterToVersion2:

    @classmethod
    def _processDidMethods(cls, didMethods):
        if 'd' in didMethods:
            d = didMethods['d']
            if isinstance(d, dict) and 'sovrin' in d:
                d['indy'] = d.pop('sovrin')

    @classmethod
    def _processDidMethod(cls, didMethod):
        if 'name' in didMethod and isinstance(didMethod['name'], str):
            didMethod['name'] = \
                didMethod['name'].replace('sovrin', 'indy')
        if 'pattern' in didMethod and isinstance(didMethod['pattern'], str):
            didMethod['pattern'] = \
                didMethod['pattern'].replace('sovrin', 'indy')

    @classmethod
    def _traverseDict(cls, d):
        if tags.OBJECT in d:
            if d[tags.OBJECT] == 'plenum.common.did_method.DidMethods':
                cls._processDidMethods(d)
            if d[tags.OBJECT] == 'plenum.common.did_method.DidMethod':
                cls._processDidMethod(d)

            if isinstance(d[tags.OBJECT], str):
                d[tags.OBJECT] = \
                    d[tags.OBJECT].replace('sovrin_common', 'indy_common')
                d[tags.OBJECT] = \
                    d[tags.OBJECT].replace('sovrin_client', 'indy_client')

        for key in d:
            cls._traverseObject(d[key])

    @classmethod
    def _traverseList(cls, l):
        for item in l:
            cls._traverseObject(item)

    @classmethod
    def _traverseObject(cls, v):
        if isinstance(v, dict):
            cls._traverseDict(v)
        elif isinstance(v, list):
            cls._traverseList(v)

    @classmethod
    def update(cls, raw):
        cls._traverseDict(raw)

        raw.pop(getClassVersionKeyBeforeRebranding(Wallet), None)
        raw[getClassVersionKey(Wallet)] = 2


# TODO: Maybe we should have a thinner wallet which should not have
# ProverWallet
class Wallet(PWallet, TrustAnchoring):

    # Increment the class version each time when the structure
    # of this class object is changed or the structure of objects being used
    # in this class object graph is changed. Note that the structure related to
    # inherited classes must be versioned by themselves using the same
    # mechanism, so modification of inherited classes structure does not require
    # increment of this class version.
    CLASS_VERSION = 2

    RAW_UPDATERS = {
        1: WalletRawUpdaterToVersion1.update,
        2: WalletRawUpdaterToVersion2.update
    }

    clientNotPresentMsg = "The wallet does not have a client associated with it"

    @staticmethod
    def makeRawCompatible(raw):
        """
        Updates a raw representation of a wallet from any previous version
        to the current version. If the raw representation of the wallet is
        of the current version or higher then this method does nothing.

        :param raw: the wallet's raw representation to update
        """

        # At first, call makeRawCompatible method of base class(es)
        # if it contains such the method

        rawClassVersion = raw.get(getClassVersionKey(Wallet))
        if rawClassVersion is None:
            rawClassVersion = \
                raw.get(getClassVersionKeyBeforeRebranding(Wallet), 0)

        for version in range(rawClassVersion, Wallet.CLASS_VERSION):
            Wallet.RAW_UPDATERS[version + 1](raw)

    def __init__(self,
                 name: str=None,
                 supportedDidMethods: DidMethods=None):
        PWallet.__init__(self,
                         name,
                         supportedDidMethods or DefaultDidMethods)
        TrustAnchoring.__init__(self)

        # Copy the class version to the instance for the version
        # to be serialized when the wallet is persisted
        setattr(self, getClassVersionKey(Wallet), Wallet.CLASS_VERSION)

        self._attributes = {}  # type: Dict[(str, Identifier,
        # Optional[Identifier]), Attribute]

        self.env = None     # Helps to know associated environment
        self._nodes = {}
        self._upgrades = {}
        self._pconfigs = {}

        self._connections = OrderedDict()  # type: Dict[str, Connection]
        # Note, ordered dict to make iteration deterministic

        self.knownIds = {}  # type: Dict[str, Identifier]

        # transactions not yet submitted
        self._pending = deque()  # type Tuple[Request, Tuple[str, Identifier,
        #  Optional[Identifier]]

        # pending transactions that have been prepared (probably submitted)
        self._prepared = {}  # type: Dict[(Identifier, int), Request]
        self.lastKnownSeqs = {}  # type: Dict[str, int]

        self.replyHandler = {
            ATTRIB: self._attribReply,
            GET_ATTR: self._getAttrReply,
            NYM: self._nymReply,
            GET_NYM: self._getNymReply,
            GET_TXNS: self._getTxnsReply,
            NODE: self._nodeReply,
            POOL_UPGRADE: self._poolUpgradeReply,
            POOL_CONFIG: self._poolConfigReply
        }

    @property
    def pendingCount(self):
        return len(self._pending)

    @staticmethod
    def _isMatchingName(needle, haystack):
        return needle.lower() in haystack.lower()

    # TODO: The names getMatchingLinksWithAvailableClaim and
    # getMatchingLinksWithReceivedClaim should be fixed. Difference between
    # `AvailableClaim` and `ReceivedClaim` is that for ReceivedClaim we
    # have attribute values from issuer.

    # TODO: Few of the below methods have duplicate code, need to refactor it
    def getMatchingConnectionsWithAvailableClaim(self, claimName=None):
        matchingConnectionsAndAvailableClaim = []
        for k, li in self._connections.items():
            for cl in li.availableClaims:
                if not claimName or Wallet._isMatchingName(claimName, cl[0]):
                    matchingConnectionsAndAvailableClaim.append((li, cl))
        return matchingConnectionsAndAvailableClaim

    def findAllProofRequests(self, claimReqName, connectionName=None):
        matches = []
        for k, li in self._connections.items():
            for cpr in li.proofRequests:
                if Wallet._isMatchingName(claimReqName, cpr.name):
                    if connectionName is None or Wallet._isMatchingName(
                            connectionName, li.name):
                        matches.append((li, cpr))
        return matches

    def getMatchingConnectionsWithProofReq(
            self, proofReqName, connectionName=None):
        matchingConnectionAndProofReq = []
        for k, li in self._connections.items():
            for cpr in li.proofRequests:
                if Wallet._isMatchingName(proofReqName, cpr.name):
                    if connectionName is None or Wallet._isMatchingName(
                            connectionName, li.name):
                        matchingConnectionAndProofReq.append((li, cpr))
        return matchingConnectionAndProofReq

    def addAttribute(self, attrib: Attribute):
        """
        Used to create a new attribute on Indy
        :param attrib: attribute to add
        :return: number of pending txns
        """
        self._attributes[attrib.key()] = attrib
        req = attrib.ledgerRequest()
        if req:
            self.pendRequest(req, attrib.key())
        return len(self._pending)

    def addNode(self, node: Node):
        """
        Used to add a new node on Indy
        :param node: Node
        :return: number of pending txns
        """
        self._nodes[node.id] = node
        req = node.ledgerRequest()
        if req:
            self.pendRequest(req, node.id)
        return len(self._pending)

    def doPoolUpgrade(self, upgrade: Upgrade):
        """
        Used to send a new code upgrade
        :param upgrade: upgrade data
        :return: number of pending txns
        """
        key = upgrade.key
        self._upgrades[key] = upgrade
        req = upgrade.ledgerRequest()
        if req:
            self.pendRequest(req, key)
        return len(self._pending)

    def doPoolConfig(self, pconfig: PoolConfig):
        """
        Used to send a new code upgrade
        :param PoolConfig: upgrade data
        :return: number of pending txns
        """
        key = pconfig.key
        self._pconfigs[key] = pconfig
        req = pconfig.ledgerRequest()
        if req:
            self.pendRequest(req, key)
        return len(self._pending)

    def hasAttribute(self, key: AttributeKey) -> bool:
        """
        Checks if attribute is present in the wallet
        @param key: Attribute unique key
        @return:
        """
        return bool(self.getAttribute(key))

    def getAttribute(self, key: AttributeKey):
        return self._attributes.get(key.key())

    def getNode(self, id: Identifier):
        return self._nodes.get(id)

    def getPoolUpgrade(self, key: str):
        return self._upgrades.get(key)

    def getPoolConfig(self, key: str):
        return self._pconfigs.get(key)

    def getAttributesForNym(self, idr: Identifier):
        return [a for a in self._attributes.values() if a.dest == idr]

    def addConnection(self, connection: Connection):
        self._connections[connection.key] = connection

    def addLastKnownSeqs(self, identifier, seqNo):
        self.lastKnownSeqs[identifier] = seqNo

    def getLastKnownSeqs(self, identifier):
        return self.lastKnownSeqs.get(identifier)

    def pendSyncRequests(self):
        # pendingTxnsReqs = self.getPendingTxnRequests()
        # for req in pendingTxnsReqs:
        #     self.pendRequest(req)

        # GET_TXNS is discontinued
        pass

    def preparePending(self, limit=None):
        new = {}
        count = 0
        while self._pending and (limit is None or count < limit):
            req, key = self._pending.pop()
            sreq = self.signRequest(req)
            new[req.identifier, req.reqId] = sreq, key
            count += 1
        self._prepared.update(new)
        # Return request in the order they were submitted
        return sorted([req for req, _ in new.values()],
                      key=operator.attrgetter("reqId"))

    def handleIncomingReply(self, observer_name, reqId, frm, result,
                            numReplies):
        """
        Called by an external entity, like a Client, to notify of incoming
        replies
        :return:
        """
        preparedReq = self._prepared.get((result[IDENTIFIER], reqId))
        if not preparedReq:
            raise RuntimeError('no matching prepared value for {},{}'.
                               format(result[IDENTIFIER], reqId))
        typ = result.get(TXN_TYPE)
        if typ and typ in self.replyHandler:
            self.replyHandler[typ](result, preparedReq)
            # else:
            #    raise NotImplementedError('No handler for {}'.format(typ))

    def _attribReply(self, result, preparedReq):
        _, attrKey = preparedReq
        attrib = self.getAttribute(AttributeKey(*attrKey))
        attrib.seqNo = result[F.seqNo.name]

    def _getAttrReply(self, result, preparedReq):
        # TODO: Confirm if we need to add the retrieved attribute to the wallet.
        # If yes then change the graph query on node to return the sequence
        # number of the attribute txn too.
        _, attrKey = preparedReq
        attrib = self.getAttribute(AttributeKey(*attrKey))
        if DATA in result:
            attrib.value = result[DATA]
            attrib.seqNo = result[F.seqNo.name]
        else:
            logger.debug("No attribute found")

    def _nymReply(self, result, preparedReq):
        target = result[TARGET_NYM]
        idy = self._trustAnchored.get(target)
        if idy:
            idy.seqNo = result[F.seqNo.name]
        else:
            logger.warning(
                "Target {} not found in trust anchored".format(target))

    def _nodeReply(self, result, preparedReq):
        _, nodeKey = preparedReq
        node = self.getNode(nodeKey)
        node.seqNo = result[F.seqNo.name]

    def _poolUpgradeReply(self, result, preparedReq):
        _, upgKey = preparedReq
        upgrade = self.getPoolUpgrade(upgKey)
        upgrade.seqNo = result[F.seqNo.name]

    def _poolConfigReply(self, result, preparedReq):
        _, cfgKey = preparedReq
        pconf = self.getPoolConfig(cfgKey)
        pconf.seqNo = result[F.seqNo.name]

    def _getNymReply(self, result, preparedReq):
        jsonData = result.get(DATA)
        if jsonData:
            data = json.loads(jsonData)
            nym = data.get(TARGET_NYM)
            idy = self.knownIds.get(nym)
            if idy:
                idy.role = data.get(ROLE) or None
                idy.trustAnchor = data.get(f.IDENTIFIER.nm)
                idy.last_synced = datetime.datetime.utcnow()
                idy.verkey = data.get(VERKEY)
                # TODO: THE GET_NYM reply should contain the sequence number of
                # the NYM transaction

    def _getTxnsReply(self, result, preparedReq):
        # TODO
        pass

    def pendRequest(self, req, key=None):
        self._pending.appendleft((req, key))

    def getConnectionInvitation(self, name: str):
        return self._connections.get(name)

    def getMatchingConnections(self, name: str) -> List[Connection]:
        allMatched = []
        for k, v in self._connections.items():
            if self._isMatchingName(name, k):
                allMatched.append(v)
        return allMatched

    # TODO: sender by default should be `self.defaultId`
    def requestAttribute(self, attrib: Attribute, sender):
        """
        Used to get a raw attribute from Indy
        :param attrib: attribute to add
        :return: number of pending txns
        """
        self._attributes[attrib.key()] = attrib
        req = attrib.getRequest(sender)
        if req:
            return self.prepReq(req, key=attrib.key())

    def requestSchema(self, nym, name, version, sender):
        """
        Used to get a schema from Indy
        :param nym: nym that schema is attached to
        :param name: name of schema
        :param version: version of schema
        :return: req object
        """
        operation = {TARGET_NYM: nym,
                     TXN_TYPE: GET_SCHEMA,
                     DATA: {NAME: name,
                            VERSION: version}
                     }

        req = Request(sender,
                      operation=operation,
                      protocolVersion=CURRENT_PROTOCOL_VERSION)
        return self.prepReq(req)

    def requestClaimDef(self, seqNo, signature, sender):
        """
        Used to get a claim def from Indy
        :param seqNo: reference number of schema
        :param signature: CL is only supported option currently
        :return: req object
        """
        operation = {TXN_TYPE: GET_CLAIM_DEF,
                     ORIGIN: sender,
                     REF: seqNo,
                     SIGNATURE_TYPE: signature
                     }

        req = Request(sender,
                      operation=operation,
                      protocolVersion=CURRENT_PROTOCOL_VERSION)
        return self.prepReq(req)

    # TODO: sender by default should be `self.defaultId`
    def requestIdentity(self, identity: Identity, sender):
        # Used to get a nym from Indy
        self.knownIds[identity.identifier] = identity
        req = identity.getRequest(sender)
        if req:
            return self.prepReq(req)

    def prepReq(self, req, key=None):
        self.pendRequest(req, key=key)
        return self.preparePending(limit=1)[0]

    def getConnection(self, name, required=False) -> Connection:
        l = self._connections.get(name)
        if not l and required:
            logger.debug("Wallet has connections {}".format(self._connections))
            raise ConnectionNotFound(name)
        return l

    def getConnectionBy(self,
                        remote: Identifier=None,
                        nonce=None,
                        internalId=None,
                        required=False) -> Optional[Connection]:
        for _, li in self._connections.items():
            if (not remote or li.remoteIdentifier == remote) and \
               (not nonce or li.request_nonce == nonce) and \
               (not internalId or li.internalId == internalId):
                return li
        if required:
            raise ConnectionNotFound

    def getIdentity(self, idr):
        # TODO, Question: Should it consider self owned identities too or
        # should it just have identities that are retrieved from the DL
        return self.knownIds.get(idr)

    def getConnectionNames(self):
        return list(self._connections.keys())

    def build_attrib(self, nym, raw=None, enc=None, hsh=None):
        assert int(bool(raw)) + int(bool(enc)) + int(bool(hsh)) == 1
        if raw:
            # l = LedgerStore.RAW
            data = raw
        elif enc:
            # l = LedgerStore.ENC
            data = enc
        elif hsh:
            # l = LedgerStore.HASH
            data = hsh
        else:
            raise RuntimeError('One of raw, enc, or hash are required.')

        # TODO looks like a possible error why we do not use `l` (see above)?
        return Attribute(randomString(5), data, self.defaultId,
                         dest=nym, ledgerStore=LedgerStore.RAW)
