{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = [
    (pkgs.python312.withPackages (ps: with ps; [
      gevent msgpack base58 merkletools rsa pysocks pyasn1 websocket-client
      gevent-websocket rencode bitcoinlib maxminddb pyopenssl rich defusedxml
      pyaes coincurve
    ]))
  ];
}
