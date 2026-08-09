#pragma once

namespace lhal {

// A single digital I/O pin. Pin muxing and direction are configured by board
// bring-up code (or the backend); this interface covers runtime use only.
class Gpio {
 public:
  virtual ~Gpio() = default;

  virtual void Write(bool level) = 0;
  virtual bool Read() const = 0;
  virtual void Toggle() { Write(!Read()); }
};

}  // namespace lhal
