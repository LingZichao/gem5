#ifndef __MEM_UACC_UACC_SELECTOR_HH__
#define __MEM_UACC_UACC_SELECTOR_HH__

#include <cstdint>
#include <deque>
#include <memory>
#include <unordered_map>
#include <unordered_set>
#include <vector>

#include "base/types.hh"
#include "mem/port.hh"
#include "params/UACCSelector.hh"
#include "sim/clocked_object.hh"
#include "sim/eventq.hh"

namespace gem5
{

class UACCController;
class UACCRequestExtension;

class UACCSelector : public ClockedObject
{
  private:
    class CpuSidePort : public ResponsePort
    {
      private:
        UACCSelector &selector;

      public:
        CpuSidePort(const std::string &name, UACCSelector &selector);

      protected:
        bool recvTimingReq(PacketPtr pkt) override;
        Tick recvAtomic(PacketPtr pkt) override;
        Tick recvAtomicBackdoor(PacketPtr pkt,
                                MemBackdoorPtr &backdoor) override;
        void recvFunctional(PacketPtr pkt) override;
        bool tryTiming(PacketPtr pkt) override;
        bool recvTimingSnoopResp(PacketPtr pkt) override;
        void recvRespRetry() override;
        AddrRangeList getAddrRanges() const override;
    };

    class DownstreamPort : public RequestPort
    {
      private:
        UACCSelector &selector;
        const bool remote;

      public:
        DownstreamPort(const std::string &name, UACCSelector &selector,
                       bool remote);

        bool isSnooping() const override { return !remote; }

      protected:
        bool recvTimingResp(PacketPtr pkt) override;
        void recvReqRetry() override;
        void recvTimingSnoopReq(PacketPtr pkt) override;
        Tick recvAtomicSnoop(PacketPtr pkt) override;
        void recvFunctionalSnoop(PacketPtr pkt) override;
        void recvRetrySnoopResp() override;
    };

    CpuSidePort cpuSidePort;
    DownstreamPort remoteSidePort;
    DownstreamPort memSidePort;

    UACCController *controller;
    const unsigned coreId;
    const unsigned cacheLineSize;
    const unsigned localSets;
    const unsigned localAssoc;
    const bool atomicSwap;

    struct LineKey
    {
        Addr addr;
        bool secure;

        bool operator==(const LineKey &other) const
        {
            return addr == other.addr && secure == other.secure;
        }
    };

    struct LineKeyHash
    {
        std::size_t operator()(const LineKey &key) const
        {
            return std::hash<Addr>()(key.addr) ^
                (std::hash<bool>()(key.secure) << 1);
        }
    };

    struct LocalLine
    {
        std::vector<uint8_t> data;
        bool secure = false;
        bool dirty = false;
        bool writable = true;
        uint64_t lastUse = 0;
    };

    struct PendingProbe
    {
        PacketPtr original = nullptr;
        PacketPtr probe = nullptr;
        unsigned set = 0;
        bool remote = false;
    };

    std::unordered_map<LineKey, LocalLine, LineKeyHash> localLines;
    std::unordered_map<PacketPtr, PendingProbe> pendingProbes;
    std::unordered_set<unsigned> busySets;
    std::deque<PacketPtr> pendingWritebacks;
    PacketPtr blockedCpuResponse = nullptr;
    uint64_t lruSequence = 0;

    enum class MemRetryKind
    {
        None,
        CpuRequest,
        Writeback
    };

    // A RequestPort may have only one packet waiting for a downstream retry.
    // Keep the owner explicit so a dirty-victim writeback cannot be injected
    // while an ordinary CPU request is the packet the XBar is retrying.
    MemRetryKind memRetryKind = MemRetryKind::None;
    bool cpuRetryPending = false;
    EventFunctionWrapper writebackEvent;

    bool responseRetryRemote = false;

    bool useRemote(PacketPtr pkt) const;
    std::shared_ptr<UACCRequestExtension> annotate(PacketPtr pkt,
                                                    bool swapProbe) const;
    RequestPort &downstream(bool remote);
    const RequestPort &downstream(bool remote) const;
    void recordForwarded(Addr addr, bool trackMiss, bool remote);

    bool localCacheRequest(PacketPtr pkt) const;
    unsigned setIndex(Addr addr) const;
    LineKey lineKey(PacketPtr pkt) const;
    LineKey lineKey(Addr addr, bool secure) const;
    LocalLine *findLocalLine(const LineKey &key);
    LocalLine *findVictim(unsigned set);
    void touch(LocalLine &line);
    void prepareVictim(const std::shared_ptr<UACCRequestExtension> &ext,
                       unsigned set);
    PacketPtr makeProbe(PacketPtr original, bool remote,
                        std::shared_ptr<UACCRequestExtension> &ext);
    bool sendProbe(PacketPtr original, bool remote);
    bool serveLocal(PacketPtr pkt, LocalLine &line);
    void enqueueWriteback(const LineKey &key, const LocalLine &line);
    void tryWriteback();
    void installLocalLine(const LineKey &key, const uint8_t *data,
                          bool dirty, bool writable, bool swapped);
    bool finishTimingResponse(PacketPtr pkt);
    bool finishTimingResponse(PacketPtr probe, bool remote);
    Tick finishAtomicProbe(PacketPtr original, PacketPtr probe,
                           bool remote, Tick latency);
    bool recvTimingReq(PacketPtr pkt);
    Tick recvAtomic(PacketPtr pkt);
    Tick recvAtomicBackdoor(PacketPtr pkt, MemBackdoorPtr &backdoor);
    void recvFunctional(PacketPtr pkt);
    bool tryTiming(PacketPtr pkt);
    bool recvTimingResp(PacketPtr pkt, bool remote);
    void recvReqRetry(bool remote);
    bool recvTimingSnoopResp(PacketPtr pkt);
    void recvRespRetry();
    void recvTimingSnoopReq(PacketPtr pkt, bool remote);
    Tick recvAtomicSnoop(PacketPtr pkt, bool remote);
    void recvFunctionalSnoop(PacketPtr pkt, bool remote);
    void recvRetrySnoopResp(bool remote);

  public:
    PARAMS(UACCSelector);
    UACCSelector(const Params &p);

    void init() override;
    Port &getPort(const std::string &if_name,
                  PortID idx = InvalidPortID) override;
};

} // namespace gem5

#endif // __MEM_UACC_UACC_SELECTOR_HH__
