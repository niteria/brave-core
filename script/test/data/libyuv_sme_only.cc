// Copyright (c) 2026 The Brave Authors. All rights reserved.
// This Source Code Form is subject to the terms of the Mozilla Public
// License, v. 2.0. If a copy of the MPL was not distributed with this file,
// You can obtain one at https://mozilla.org/MPL/2.0/.

// Freestanding Linux fixture for libyuv's real scalar and SME rows. Supply the
// actual kernel auxiliary vector to compiler-rt; no feature masking or fake
// SME ABI helpers. This avoids requiring a target libc/rootfs under QEMU.
#include "libyuv/convert_argb.h"
#include "libyuv/row.h"

namespace {
const unsigned long* auxiliary;

[[noreturn]] void Exit(unsigned long result) {
  register unsigned long x0 asm("x0") = result;
  register unsigned long x8 asm("x8") = 93;  // Linux exit
  asm volatile("svc 0" : : "r"(x0), "r"(x8) : "memory");
  __builtin_unreachable();
}
}  // namespace

extern "C" {
unsigned long getauxval(unsigned long type) {
  for (const unsigned long* p = auxiliary; p[0]; p += 2) {
    if (p[0] == type) {
      return p[1];
    }
  }
  return 0;
}

unsigned long __stack_chk_guard = 0xa5a5a5a5;
[[noreturn]] void __stack_chk_fail() {
  Exit(2);
}
__attribute__((noreturn)) void abort() noexcept {
  Exit(3);
}
void __init_cpu_features();
}

asm(".global _start\n_start:\nmov x0, sp\nbl test_main\nbrk #0\n");

extern "C" [[noreturn]] void test_main(const unsigned long* stack) {
  const unsigned long* p = stack + 1 + stack[0] + 1;
  while (*p) {
    ++p;
  }
  auxiliary = p + 1;
  __init_cpu_features();
  if (!(getauxval(26) & (1UL << 23))) {  // AT_HWCAP2 / HWCAP2_SME
    Exit(4);
  }
  const bool expect_sve = stack[0] > 1 &&
                         reinterpret_cast<const char*>(stack[2])[0] == '1';
  if (!!(getauxval(16) & (1UL << 22)) != expect_sve) {
    Exit(5);  // AT_HWCAP / HWCAP_SVE disagrees with the test scenario.
  }

  unsigned char y[257], u[257], v[257], actual[4 * 257], expected[4 * 257];
  for (int i = 0; i < 257; ++i) {
    y[i] = i * 13;
    u[i] = i * 17;
    v[i] = i * 23;
  }
  const int widths[] = {1, 3, 31, 32, 63, 64, 257};
  for (int width : widths) {
    for (int i = 0; i < 4 * 257; ++i) {
      actual[i] = expected[i] = 0xa5;
    }
    libyuv::I444ToARGBRow_SME(y, u, v, actual, &libyuv::kYuvI601Constants,
                            width);
    libyuv::I444ToARGBRow_C(y, u, v, expected, &libyuv::kYuvI601Constants,
                          width);
    for (int i = 0; i < 4 * 257; ++i) {
      if (actual[i] != expected[i]) {
        Exit(1);
      }
    }
  }
  Exit(0);
}
