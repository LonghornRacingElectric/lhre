#pragma once

#include <algorithm>
#include <deque>
#include <vector>

#include "lhal/can.hpp"

namespace lhal::host {

class Can;

// A virtual CAN bus: frames sent by one attached node are delivered to every
// other attached node.
class CanNetwork {
 public:
  void Attach(Can* node) { nodes_.push_back(node); }
  void Detach(Can* node) {
    nodes_.erase(std::remove(nodes_.begin(), nodes_.end(), node),
                 nodes_.end());
  }

 private:
  friend class Can;
  void Broadcast(const Can* sender, const CanFrame& frame);

  std::vector<Can*> nodes_;
};

// Host CAN node. Without a network, sends succeed but go nowhere (still
// recorded in sent() for inspection).
class Can final : public lhal::CanBus {
 public:
  Can() = default;
  explicit Can(CanNetwork* network) : network_(network) {
    network_->Attach(this);
  }
  ~Can() override {
    if (network_ != nullptr) {
      network_->Detach(this);
    }
  }

  Can(const Can&) = delete;
  Can& operator=(const Can&) = delete;

  Status Send(const CanFrame& frame) override {
    sent_.push_back(frame);
    if (network_ != nullptr) {
      network_->Broadcast(this, frame);
    }
    return Status::kOk;
  }

  bool Receive(CanFrame* out) override {
    if (rx_.empty()) {
      return false;
    }
    *out = rx_.front();
    rx_.pop_front();
    return true;
  }

  void SetRxCallback(RxCallback callback, void* context) override {
    rx_callback_ = callback;
    rx_context_ = context;
  }

  // Test helpers -----------------------------------------------------------
  const std::vector<CanFrame>& sent() const { return sent_; }
  void ClearSent() { sent_.clear(); }
  // Deliver a frame to this node as if it arrived on the bus.
  void Inject(const CanFrame& frame) { Deliver(frame); }

 private:
  friend class CanNetwork;
  void Deliver(const CanFrame& frame) {
    if (rx_callback_ != nullptr) {
      rx_callback_(rx_context_, frame);
    } else {
      rx_.push_back(frame);
    }
  }

  CanNetwork* network_ = nullptr;
  std::deque<CanFrame> rx_;
  std::vector<CanFrame> sent_;
  RxCallback rx_callback_ = nullptr;
  void* rx_context_ = nullptr;
};

inline void CanNetwork::Broadcast(const Can* sender, const CanFrame& frame) {
  for (Can* node : nodes_) {
    if (node != sender) {
      node->Deliver(frame);
    }
  }
}

}  // namespace lhal::host
