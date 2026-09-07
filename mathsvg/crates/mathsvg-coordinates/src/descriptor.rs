use mathsvg_core::{
    checked_u64_add, checked_u64_mul, encoded_len, Cursor, Error, Limits, Result, Writer,
};

pub const STRIDE_OPCODE: u8 = 0x41;
pub const BYTE_PLANE_OPCODE: u8 = 0x42;
pub const BIT_PLANE_OPCODE: u8 = 0x43;

/// Hard bounded-phase limits. They are deliberately lower than anything that
/// could make one block's indexing arithmetic unbounded.
pub const MAX_CHANNELS: u16 = 4_096;
pub const MAX_RECORD_BYTES: u64 = 65_536;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
#[repr(u8)]
pub enum Endianness {
    Little = 0,
    Big = 1,
}

impl Endianness {
    fn parse(value: u8) -> Result<Self> {
        match value {
            0 => Ok(Self::Little),
            1 => Ok(Self::Big),
            _ => Err(Error::InvalidValue("invalid BYTE_PLANE endianness")),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum CoordinateTransform {
    Identity,
    Stride { element_width: u8, channels: u16 },
    BytePlane { word_width: u8, endian: Endianness },
    BitPlane,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct CoordinateDescriptor {
    pub original_bytes: u64,
    pub transform: CoordinateTransform,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CoordinateAnalysis {
    pub original_bytes: u64,
    pub transformed_bytes: u64,
    pub inverse_work: u64,
    pub temporary_bytes: u64,
    pub coordinate_metadata_bytes: u64,
}

impl CoordinateDescriptor {
    pub const fn identity(original_bytes: u64) -> Self {
        Self {
            original_bytes,
            transform: CoordinateTransform::Identity,
        }
    }

    pub fn validate(&self, limits: &Limits) -> Result<()> {
        limits.check(
            "coordinate input bytes",
            self.original_bytes,
            limits.max_output_bytes,
        )?;
        limits.check(
            "coordinate block input bytes",
            self.original_bytes,
            limits.max_block_output_bytes,
        )?;

        match self.transform {
            CoordinateTransform::Identity | CoordinateTransform::BitPlane => {}
            CoordinateTransform::Stride {
                element_width,
                channels,
            } => {
                validate_width(element_width)?;
                if !(2..=MAX_CHANNELS).contains(&channels) {
                    return Err(Error::InvalidValue(
                        "STRIDE channels are outside the bounded range",
                    ));
                }
                let record_bytes = checked_u64_mul(
                    u64::from(element_width),
                    u64::from(channels),
                    "STRIDE record bytes",
                )?;
                if record_bytes > MAX_RECORD_BYTES {
                    return Err(Error::LimitExceeded {
                        what: "STRIDE record bytes",
                        actual: record_bytes,
                        limit: MAX_RECORD_BYTES,
                    });
                }
                if !is_multiple(self.original_bytes, record_bytes) {
                    return Err(Error::InvalidValue(
                        "STRIDE input length is not a whole record count",
                    ));
                }
            }
            CoordinateTransform::BytePlane { word_width, endian } => {
                validate_width(word_width)?;
                if word_width == 1 && endian != Endianness::Little {
                    return Err(Error::InvalidValue(
                        "one-byte BYTE_PLANE has canonical little endian only",
                    ));
                }
                if !is_multiple(self.original_bytes, u64::from(word_width)) {
                    return Err(Error::InvalidValue(
                        "BYTE_PLANE input length is not a whole word count",
                    ));
                }
            }
        }

        let transformed_bytes = self.transformed_bytes()?;
        limits.check(
            "coordinate transformed bytes",
            transformed_bytes,
            limits.max_output_bytes,
        )?;
        limits.check(
            "coordinate block transformed bytes",
            transformed_bytes,
            limits.max_block_output_bytes,
        )?;
        limits.check(
            "parameter bytes",
            self.parameter_payload().len() as u64,
            limits.max_parameter_bytes,
        )?;
        limits.check("work", self.inverse_work()?, limits.max_work)
    }

    pub fn transformed_bytes(&self) -> Result<u64> {
        match self.transform {
            CoordinateTransform::Identity
            | CoordinateTransform::Stride { .. }
            | CoordinateTransform::BytePlane { .. } => Ok(self.original_bytes),
            CoordinateTransform::BitPlane => {
                let plane_bytes =
                    checked_u64_add(self.original_bytes, 7, "BIT_PLANE packed length")? / 8;
                checked_u64_mul(plane_bytes, 8, "BIT_PLANE transformed bytes")
            }
        }
    }

    pub fn inverse_work(&self) -> Result<u64> {
        let per_byte = match self.transform {
            CoordinateTransform::BitPlane => {
                checked_u64_mul(self.original_bytes, 8, "BIT_PLANE inverse work")?
            }
            CoordinateTransform::Identity
            | CoordinateTransform::Stride { .. }
            | CoordinateTransform::BytePlane { .. } => self.original_bytes,
        };
        checked_u64_add(1, per_byte, "coordinate inverse work")
    }

    /// Exact coordinate-node overhead when the canonical embedded child record
    /// has `child_record_bytes` bytes.
    pub fn dsl_metadata_bytes(&self, child_record_bytes: u64) -> Result<u64> {
        if self.transform == CoordinateTransform::Identity {
            return Ok(0);
        }
        let parameter_bytes = self.parameter_payload().len() as u64;
        let payload_bytes = checked_u64_add(
            parameter_bytes,
            child_record_bytes,
            "coordinate node payload",
        )?;
        checked_u64_add(
            checked_u64_add(
                2,
                encoded_len(payload_bytes) as u64,
                "coordinate node framing",
            )?,
            parameter_bytes,
            "coordinate metadata bytes",
        )
    }

    pub fn analyze(&self, child_record_bytes: u64, limits: &Limits) -> Result<CoordinateAnalysis> {
        self.validate(limits)?;
        Ok(CoordinateAnalysis {
            original_bytes: self.original_bytes,
            transformed_bytes: self.transformed_bytes()?,
            inverse_work: self.inverse_work()?,
            temporary_bytes: 0,
            coordinate_metadata_bytes: self.dsl_metadata_bytes(child_record_bytes)?,
        })
    }

    /// Canonical standalone descriptor record used by discovery ledgers and
    /// deterministic cache keys.
    pub fn encode(&self, limits: &Limits) -> Result<Vec<u8>> {
        let payload = self.encode_parameters(limits)?;
        let mut output = Writer::new();
        output.write_u8(self.catalog_opcode());
        output.write_u8(0);
        output.write_usize(payload.len());
        output.write_raw(&payload);
        Ok(output.into_inner())
    }

    pub fn decode(input: &[u8], limits: &Limits) -> Result<Self> {
        let mut cursor = Cursor::new(input);
        let opcode = cursor.read_u8("coordinate descriptor opcode")?;
        let flags = cursor.read_u8("coordinate descriptor flags")?;
        if flags != 0 {
            return Err(Error::InvalidFlags { opcode, flags });
        }
        let payload_length = cursor.read_usize("coordinate descriptor payload")?;
        let mut payload =
            cursor.subcursor(payload_length, "coordinate descriptor parameter payload")?;

        let descriptor = Self::decode_parameters(opcode, &mut payload, limits)?;
        payload.finish("coordinate descriptor parameter payload")?;
        cursor.finish("coordinate descriptor")?;
        Ok(descriptor)
    }

    /// Encode only the canonical coordinate parameters. A DSL coordinate node
    /// writes these bytes directly before its embedded child record.
    pub fn encode_parameters(&self, limits: &Limits) -> Result<Vec<u8>> {
        self.validate(limits)?;
        let payload = self.parameter_payload();
        limits.check(
            "parameter bytes",
            payload.len() as u64,
            limits.max_parameter_bytes,
        )?;
        Ok(payload)
    }

    /// Decode one coordinate parameter prefix from a larger node payload.
    ///
    /// Unlike [`Self::decode`], this intentionally leaves the remaining bytes
    /// in `cursor` for the caller to parse as the embedded child node.
    pub fn decode_parameters(opcode: u8, cursor: &mut Cursor<'_>, limits: &Limits) -> Result<Self> {
        let start = cursor.position();
        let original_bytes = cursor.read_u64()?;
        let transform = match opcode {
            0x00 => CoordinateTransform::Identity,
            STRIDE_OPCODE => {
                let element_width = read_u8_value(cursor, "STRIDE element width")?;
                let channels = read_u16_value(cursor, "STRIDE channels")?;
                CoordinateTransform::Stride {
                    element_width,
                    channels,
                }
            }
            BYTE_PLANE_OPCODE => {
                let word_width = read_u8_value(cursor, "BYTE_PLANE word width")?;
                let endian = Endianness::parse(cursor.read_u8("BYTE_PLANE endianness")?)?;
                CoordinateTransform::BytePlane { word_width, endian }
            }
            BIT_PLANE_OPCODE => CoordinateTransform::BitPlane,
            0xc0..=0xff => return Err(Error::InvalidOpcode(opcode)),
            _ => return Err(Error::UnsupportedOpcode(opcode)),
        };
        let parameter_bytes =
            cursor
                .position()
                .checked_sub(start)
                .ok_or(Error::IntegerOverflow {
                    context: "coordinate parameter bytes",
                })?;
        limits.check(
            "parameter bytes",
            parameter_bytes as u64,
            limits.max_parameter_bytes,
        )?;
        let descriptor = Self {
            original_bytes,
            transform,
        };
        descriptor.validate(limits)?;
        Ok(descriptor)
    }

    pub const fn catalog_opcode(&self) -> u8 {
        match self.transform {
            CoordinateTransform::Identity => 0x00,
            CoordinateTransform::Stride { .. } => STRIDE_OPCODE,
            CoordinateTransform::BytePlane { .. } => BYTE_PLANE_OPCODE,
            CoordinateTransform::BitPlane => BIT_PLANE_OPCODE,
        }
    }

    fn parameter_payload(&self) -> Vec<u8> {
        let mut payload = Writer::new();
        payload.write_u64(self.original_bytes);
        match self.transform {
            CoordinateTransform::Identity | CoordinateTransform::BitPlane => {}
            CoordinateTransform::Stride {
                element_width,
                channels,
            } => {
                payload.write_u64(u64::from(element_width));
                payload.write_u64(u64::from(channels));
            }
            CoordinateTransform::BytePlane { word_width, endian } => {
                payload.write_u64(u64::from(word_width));
                payload.write_u8(endian as u8);
            }
        }
        payload.into_inner()
    }
}

fn validate_width(width: u8) -> Result<()> {
    if matches!(width, 1 | 2 | 3 | 4 | 6 | 8) {
        Ok(())
    } else {
        Err(Error::InvalidValue(
            "coordinate width is outside 1,2,3,4,6,8",
        ))
    }
}

fn is_multiple(value: u64, divisor: u64) -> bool {
    value.checked_rem(divisor) == Some(0)
}

fn read_u8_value(cursor: &mut Cursor<'_>, context: &'static str) -> Result<u8> {
    let value = cursor.read_u64()?;
    u8::try_from(value).map_err(|_| Error::InvalidValue(context))
}

fn read_u16_value(cursor: &mut Cursor<'_>, context: &'static str) -> Result<u16> {
    let value = cursor.read_u64()?;
    u16::try_from(value).map_err(|_| Error::InvalidValue(context))
}
