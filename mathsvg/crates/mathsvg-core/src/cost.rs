use crate::{checked_u64_add, Error, Result};

/// The complete normative ordering key for a fully serialised candidate.
///
/// Smaller is always better.  Field order exactly follows the mathematical
/// specification, so ordinary lexicographic `Ord` is the tie-break.
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct CandidateCost {
    pub archive_bytes: u64,
    pub decode_work: u64,
    pub decode_memory: u64,
    pub node_count: u64,
    pub dependency_count: u64,
    pub opcode_sequence: Vec<u8>,
    pub parameter_payload: Vec<u8>,
    /// Final deterministic fallback.  This does not supersede the preceding
    /// semantic tie-breaks; it makes the ordering total if two candidates have
    /// the same opcode and extracted-parameter sequences.
    pub canonical_payload: Vec<u8>,
}

impl CandidateCost {
    pub fn literal_upper_bound(archive_bytes: u64, parameter_payload: Vec<u8>) -> Self {
        Self {
            archive_bytes,
            decode_work: archive_bytes,
            decode_memory: archive_bytes,
            node_count: 2,
            dependency_count: 1,
            opcode_sequence: vec![0x20, 0x00],
            canonical_payload: parameter_payload.clone(),
            parameter_payload,
        }
    }
}

pub fn select_best<T>(
    candidates: impl IntoIterator<Item = (CandidateCost, T)>,
) -> Option<(CandidateCost, T)> {
    candidates
        .into_iter()
        .min_by(|(left, _), (right, _)| left.cmp(right))
}

/// Exact container accounting categories from the MathSVG cost identity.
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CostBreakdown {
    pub header: u64,
    pub coordinate: u64,
    pub topology: u64,
    pub definitions: u64,
    pub references: u64,
    pub parameters: u64,
    pub residual_layers: u64,
    pub literal_leaves: u64,
    pub entropy_metadata: u64,
    pub index: u64,
    pub footer: u64,
}

impl CostBreakdown {
    pub fn total(&self) -> Result<u64> {
        [
            self.header,
            self.coordinate,
            self.topology,
            self.definitions,
            self.references,
            self.parameters,
            self.residual_layers,
            self.literal_leaves,
            self.entropy_metadata,
            self.index,
            self.footer,
        ]
        .into_iter()
        .try_fold(0, |sum, value| {
            checked_u64_add(sum, value, "cost breakdown")
        })
    }

    pub fn verify_total(&self, actual_archive_bytes: u64) -> Result<()> {
        let predicted = self.total()?;
        if predicted == actual_archive_bytes {
            Ok(())
        } else {
            Err(Error::LengthMismatch {
                context: "cost accounting",
                expected: predicted,
                actual: actual_archive_bytes,
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> CandidateCost {
        CandidateCost {
            archive_bytes: 100,
            decode_work: 20,
            decode_memory: 10,
            node_count: 4,
            dependency_count: 3,
            opcode_sequence: vec![1, 2],
            parameter_payload: vec![3, 4],
            canonical_payload: vec![5, 6],
        }
    }

    #[test]
    fn every_normative_tie_break_field_is_ordered() {
        let preferred = base();
        let mut variants = Vec::new();

        let mut value = preferred.clone();
        value.archive_bytes += 1;
        variants.push(value);
        let mut value = preferred.clone();
        value.decode_work += 1;
        variants.push(value);
        let mut value = preferred.clone();
        value.decode_memory += 1;
        variants.push(value);
        let mut value = preferred.clone();
        value.node_count += 1;
        variants.push(value);
        let mut value = preferred.clone();
        value.dependency_count += 1;
        variants.push(value);
        let mut value = preferred.clone();
        value.opcode_sequence = vec![1, 3];
        variants.push(value);
        let mut value = preferred.clone();
        value.parameter_payload = vec![3, 5];
        variants.push(value);
        let mut value = preferred.clone();
        value.canonical_payload = vec![5, 7];
        variants.push(value);

        for worse in variants {
            assert!(preferred < worse);
        }
    }

    #[test]
    fn selects_minimum_and_checks_exact_breakdown() {
        let preferred = base();
        let mut worse = base();
        worse.archive_bytes += 1;
        let selected = select_best([(worse, "worse"), (preferred.clone(), "best")]).unwrap();
        assert_eq!(selected, (preferred, "best"));

        let breakdown = CostBreakdown {
            header: 128,
            literal_leaves: 10,
            footer: 128,
            ..CostBreakdown::default()
        };
        assert_eq!(breakdown.total().unwrap(), 266);
        breakdown.verify_total(266).unwrap();
        assert!(breakdown.verify_total(265).is_err());
    }
}
