// Package homeca generates and checks the home control-plane identity in memory.
// It has no filesystem, network, process, ambient-clock or default-random source.
// Root invocation, protected custody and TLS installation belong to the caller.
package homeca

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/hex"
	"fmt"
	"io"
	"math/big"
	"time"
)

const (
	ControlName         = "control.happyranch.invalid"
	LeafLifetime        = 365 * 24 * time.Hour
	RenewalWindow       = 90 * 24 * time.Hour
	MinimumLeafLifetime = 30 * 24 * time.Hour
	ClockSkew           = 5 * time.Minute
)

// Error is a closed refusal category. No underlying parser, signer or reader
// error is retained: those errors may contain private input or reader content.
type Error uint8

const (
	ErrCA Error = iota + 1
	ErrCAValidity
	ErrInsufficientCALifetime
	ErrLeaf
	ErrPrivateKey
	ErrRandomness
	ErrGeneration
	ErrTime
)

func (e Error) Error() string {
	switch e {
	case ErrCA:
		return "homeca: invalid CA"
	case ErrCAValidity:
		return "homeca: CA outside validity window"
	case ErrInsufficientCALifetime:
		return "homeca: insufficient CA lifetime"
	case ErrLeaf:
		return "homeca: invalid leaf"
	case ErrPrivateKey:
		return "homeca: invalid private key"
	case ErrRandomness:
		return "homeca: randomness unavailable"
	case ErrGeneration:
		return "homeca: generation refused"
	case ErrTime:
		return "homeca: invalid issuance time"
	default:
		return "homeca: refused"
	}
}

// PrivateKey holds PKCS#8 DER. All fmt verbs (including hex and %#v) redact it.
// It deliberately has no implicit serialization method. DER is the explicit
// sensitive transport/custody boundary; callers must never log its result.
type PrivateKey struct{ der []byte }

func (PrivateKey) String() string               { return "homeca.PrivateKey(<redacted>)" }
func (k PrivateKey) GoString() string           { return k.String() }
func (k PrivateKey) Format(s fmt.State, _ rune) { _, _ = io.WriteString(s, k.String()) }
func (k PrivateKey) DER() []byte                { return bytes.Clone(k.der) }

// ImportPrivateKey copies a previously protected PKCS#8 ECDSA P-256 key.
// Parsing errors are replaced by a fixed category, never wrapped.
func ImportPrivateKey(der []byte) (PrivateKey, error) {
	if _, err := parseKey(der); err != nil {
		return PrivateKey{}, err
	}
	return PrivateKey{der: bytes.Clone(der)}, nil
}

func parseKey(der []byte) (*ecdsa.PrivateKey, error) {
	if len(der) > 4096 {
		return nil, ErrPrivateKey
	}
	k, err := x509.ParsePKCS8PrivateKey(der)
	if err != nil {
		return nil, ErrPrivateKey
	}
	key, ok := k.(*ecdsa.PrivateKey)
	if !ok || key.Curve != elliptic.P256() {
		return nil, ErrPrivateKey
	}
	return key, nil
}

func serial(random io.Reader) (*big.Int, error) {
	var b [20]byte
	if random == nil {
		return nil, ErrRandomness
	}
	if _, err := io.ReadFull(random, b[:]); err != nil {
		return nil, ErrRandomness
	}
	b[0] &= 0x7f // 159 random bits, positive DER INTEGER within RFC 5280's 20 octets.
	n := new(big.Int).SetBytes(b[:])
	if n.Sign() == 0 {
		return nil, ErrRandomness
	}
	return n, nil
}

func issuanceTime(now time.Time) (time.Time, error) {
	now = now.UTC().Truncate(time.Second) // X.509 encodes whole seconds.
	if now.Year() < 2 || now.Year() > 9989 {
		return time.Time{}, ErrTime
	}
	return now, nil
}

func generate(template, parent *x509.Certificate, signer *ecdsa.PrivateKey, random io.Reader) ([]byte, PrivateKey, error) {
	k, err := ecdsa.GenerateKey(elliptic.P256(), random)
	if err != nil {
		return nil, PrivateKey{}, ErrRandomness
	}
	if signer == nil {
		signer = k
	} // NewCA only; IssueLeaf always supplies the admitted CA key.
	der, err := x509.CreateCertificate(random, template, parent, &k.PublicKey, signer)
	if err != nil {
		return nil, PrivateKey{}, ErrGeneration
	}
	keyDER, err := x509.MarshalPKCS8PrivateKey(k)
	if err != nil {
		return nil, PrivateKey{}, ErrGeneration
	}
	return der, PrivateKey{der: keyDER}, nil
}

// NewCA creates a self-signed, leaf-only ECDSA P-256 issuer, valid for ten
// calendar years from now, backdated five minutes. CA replacement is an explicit
// home-identity change requiring turn-off/re-enable, fresh invites and approval.
func NewCA(now time.Time, random io.Reader) ([]byte, PrivateKey, error) {
	now, err := issuanceTime(now)
	if err != nil {
		return nil, PrivateKey{}, err
	}
	n, err := serial(random)
	if err != nil {
		return nil, PrivateKey{}, err
	}
	c := &x509.Certificate{SerialNumber: n, Subject: pkix.Name{CommonName: "HappyRanch home CA"},
		NotBefore: now.Add(-ClockSkew), NotAfter: now.AddDate(10, 0, 0),
		BasicConstraintsValid: true, IsCA: true, MaxPathLenZero: true, KeyUsage: x509.KeyUsageCertSign}
	return generate(c, c, nil, random)
}

func admitCA(der []byte, now time.Time) (*x509.Certificate, error) {
	if len(der) > 4096 {
		return nil, ErrCA
	}
	c, err := x509.ParseCertificate(der) // Exactly one DER, no trailing bytes/PEM.
	if err != nil || !c.BasicConstraintsValid || !c.IsCA || c.KeyUsage&x509.KeyUsageCertSign == 0 {
		return nil, ErrCA
	}
	pub, ok := c.PublicKey.(*ecdsa.PublicKey)
	if !ok || pub.Curve != elliptic.P256() || !bytes.Equal(c.RawIssuer, c.RawSubject) || c.CheckSignatureFrom(c) != nil {
		return nil, ErrCA
	}
	if now.Before(c.NotBefore) || !now.Before(c.NotAfter) {
		return nil, ErrCAValidity
	}
	return c, nil
}

// IssueLeaf creates a fresh leaf/key under exactly the supplied existing CA.
// It cannot create/replace a CA. Fewer than 30 days remaining refuses with
// ErrInsufficientCALifetime; callers must never recover by silently calling NewCA.
// There is no SAN or address input: a dial_ip change does not need a new leaf.
func IssueLeaf(caDER []byte, caKey PrivateKey, now time.Time, random io.Reader) ([]byte, PrivateKey, error) {
	ca, err := admitCA(caDER, now)
	if err != nil {
		return nil, PrivateKey{}, err
	}
	issued, err := issuanceTime(now)
	if err != nil {
		return nil, PrivateKey{}, err
	}
	if ca.NotAfter.Before(now.Add(MinimumLeafLifetime)) {
		return nil, PrivateKey{}, ErrInsufficientCALifetime
	}
	k, err := parseKey(caKey.der)
	if err != nil {
		return nil, PrivateKey{}, err
	}
	if !k.PublicKey.Equal(ca.PublicKey) {
		return nil, PrivateKey{}, ErrPrivateKey
	}
	n, err := serial(random)
	if err != nil {
		return nil, PrivateKey{}, err
	}
	notBefore, notAfter := issued.Add(-ClockSkew), issued.Add(LeafLifetime)
	if notBefore.Before(ca.NotBefore) {
		notBefore = ca.NotBefore
	}
	if notAfter.After(ca.NotAfter) {
		notAfter = ca.NotAfter
	}
	leaf := &x509.Certificate{SerialNumber: n, Subject: pkix.Name{CommonName: ControlName},
		DNSNames: []string{ControlName}, NotBefore: notBefore, NotAfter: notAfter,
		BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	return generate(leaf, ca, k, random)
}

// Check validates one current server leaf to this exact CA using an isolated
// root pool, standard DNS/time/serverAuth verification and a closed SAN profile.
func Check(caDER, leafDER []byte, now time.Time) error {
	ca, err := admitCA(caDER, now)
	if err != nil {
		return err
	}
	if len(leafDER) > 4096 {
		return ErrLeaf
	}
	leaf, err := x509.ParseCertificate(leafDER)
	if err != nil || !leaf.BasicConstraintsValid || leaf.IsCA || leaf.KeyUsage&x509.KeyUsageDigitalSignature == 0 ||
		len(leaf.ExtKeyUsage) != 1 || leaf.ExtKeyUsage[0] != x509.ExtKeyUsageServerAuth || len(leaf.UnknownExtKeyUsage) != 0 ||
		leaf.NotBefore.Before(ca.NotBefore) || leaf.NotAfter.After(ca.NotAfter) || now.Before(leaf.NotBefore) || !now.Before(leaf.NotAfter) {
		return ErrLeaf
	}
	// Inspect the actual extension too: x509's typed SAN slices omit unknown
	// GeneralName types. Exactly one primitive dNSName excludes all other SANs.
	fixedSAN := false
	for _, ext := range leaf.Extensions {
		if ext.Id.Equal(asn1.ObjectIdentifier{2, 5, 29, 17}) {
			var names []asn1.RawValue
			rest, err := asn1.Unmarshal(ext.Value, &names)
			fixedSAN = err == nil && len(rest) == 0 && len(names) == 1 && names[0].Class == 2 && names[0].Tag == 2 &&
				!names[0].IsCompound && string(names[0].Bytes) == ControlName
		}
	}
	if !fixedSAN || len(leaf.DNSNames) != 1 || leaf.DNSNames[0] != ControlName {
		return ErrLeaf
	}
	roots := x509.NewCertPool()
	roots.AddCert(ca)
	if leaf.CheckSignatureFrom(ca) != nil {
		return ErrLeaf
	}
	if _, err := leaf.Verify(x509.VerifyOptions{Roots: roots, DNSName: ControlName, CurrentTime: now,
		KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}); err != nil {
		return ErrLeaf
	}
	return nil
}

// LeafRenewalDue returns true for invalid profiles or at most 90 days remaining.
// This is only a scheduling decision, never issuance or trust authority. Expired
// CA/leaf still refuse Check; IssueLeaf can recover an expired leaf under a usable CA.
func LeafRenewalDue(leafDER, caDER []byte, now time.Time) bool {
	if Check(caDER, leafDER, now) != nil {
		return true
	}
	leaf, _ := x509.ParseCertificate(leafDER) // Check admitted exactly these bytes.
	return !now.Add(RenewalWindow).Before(leaf.NotAfter)
}

// Fingerprint hashes the exact supplied CA DER; it does not admit a certificate.
func Fingerprint(caDER []byte) string {
	sum := sha256.Sum256(caDER)
	return hex.EncodeToString(sum[:])
}
